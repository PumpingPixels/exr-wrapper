import glob
import os
import re
import shutil
import traceback

try:
    import OpenImageIO as oiio
except ImportError:
    traceback.print_exc()
    raise RuntimeError('OpenImageIO library could not be found')
    sys.exit()


def dst_path(src, output_path, overwrite):
    """
    Resolves the destination path for the wrapping operation
    :param src: single input file path
    :param output_path: output sequence path
    :param overwrite: overwrite output option
    :return: single output file path
    """
    dst = output_path
    frame = frame_of(src)
    if frame and '%' in output_path:
        dst = output_path % frame
    if os.path.isfile(dst) and not overwrite:
        raise RuntimeError('Output file {dst} already exists.'.format(dst=dst))
    else:
        return dst


def frame_of(path):
    """
    Get the frame number of a specific image path
    :param path:
    :return: frame as int
    """
    frame = re.findall('.+[\._](\d{4,8})\.[A-Za-z]{3,4}', path)
    if frame and len(frame) == 1:
        return int(frame[0])

def bu_dir(input_path, create=True):
    """
    Builds the path of the directory for backed up input files and creates it.
    :param input_path: input exr sequence
    :param create: boolean if the directory should be created
    :return: path to the backup directory
    """
    backup_dir = os.path.join(os.path.dirname(input_path), '_BAK')
    if create:
        try:
            os.mkdir(backup_dir)
            prev_backup = False
        except OSError:
            prev_backup = True
    else:
        prev_backup = os.path.isdir(backup_dir)
    return backup_dir, prev_backup


def detect_sequence(path):
    """
    Detects a sequence naming convention based on a path's embedded frame padding
    :param path: path to a single file
    :return: path to a corresponding sequence in printf notation (e.g. /path/to/image.%04d.exr)
    """
    re_find = re.findall('(\d{4,8}|#{4,8})\.(exr|EXR)', path)
    if re_find:
        padding = re_find[0][0]
        path = path.replace(padding, '%{:02d}d'.format(len(padding)))
    return path


def find_sequence(path):
    """
    Searches the filesystem to find the corresponding sequence for a single file.
    :param path: path to a single image file
    :return: image sequence as a tuple (path, first frame, last frame)
    """
    path = detect_sequence(path)
    files = find_files(path)
    if files:
        first = frame_of(files[0])
        last = frame_of(files[-1])
        return path, first, last
    else:
        return path, 0, 0


def find_files(input_path, framerange=None):
    """
    Discovers files on the filesystem.
    :param input_path: Path to the file sequence
    :param framerange: optional framerange
    :return: array of single file paths
    """
    files = []
    if '%' not in input_path:
        return [input_path]
    if framerange:
        for part_range in framerange.split(','):
            if '-' in part_range:
                first, last = part_range.split('-')
                for i in range(int(first), int(last) + 1):
                    files.append(input_path % i)
            else:
                files.append(input_path % int(part_range))
    else:
        input_path = re.sub(r'(\%0[4-8]d)(\.[exr|EXR])', r'*\2', input_path)
        files = glob.glob(input_path)
    files = sorted(files)
    return files


def split_subimages(image_in, properties):
    """
    Splits an image into various subimages based on layer names
    :param image_in: input as oiio.ImageBuf
    :param properties: dictionary of additional parameters
    :return: identical dictionary extend with arrays of sub_names, sub_specs and sub_pixels
    """
    properties['roi'].chend = 4
    channelindex = 0
    for channel_name in image_in.nativespec().channelnames:
        if channel_name in ['R', 'G', 'B', 'A']:
            properties['current_sub'] = 'rgba'
        else:
            properties['current_sub'] = channel_name.split('.')[0]
        # new subimage is found
        if (properties['recent_sub'] and properties['current_sub'] != properties[
            'recent_sub']) or channelindex + 1 == image_in.nativespec().nchannels:
            # case last channel
            if channelindex + 1 == image_in.nativespec().nchannels:
                properties['sub_ch_count'] += 1
                channelindex += 1
            properties['sub_start'] = channelindex - properties['sub_ch_count']
            properties['sub_end'] = channelindex - 1
            if properties['verbose']:
                print('Subimage found: {recent_sub} on channels: '
                      '{sub_start}-{sub_end}, channelcount: {sub_ch_count}'.format(**properties))
            if image_in.nativespec().channelformats:
                typedesc = image_in.nativespec().channelformats[properties['sub_start']]
            else:
                typedesc = image_in.nativespec().format
            subimage_spec = oiio.ImageSpec(image_in.nativespec().width, image_in.nativespec().height,
                                           properties['sub_ch_count'], typedesc)
            subimage_spec.roi = properties['roi']
            subimage_spec.full_width = image_in.nativespec().full_width
            subimage_spec.full_height = image_in.nativespec().full_height
            subimage_spec.depth = image_in.nativespec().depth
            subimage_spec.full_x = image_in.nativespec().full_x
            subimage_spec.full_y = image_in.nativespec().full_y
            subimage_spec.full_z = image_in.nativespec().full_z
            # copy metadata for the first subimage
            if properties['sub_start'] == 0:
                for i in range(len(image_in.nativespec().extra_attribs)):
                    if properties['rm_manifest'] and 'manifest' in image_in.nativespec().extra_attribs[i].name:
                        continue
                    if image_in.nativespec().extra_attribs[i].type in ['string', 'int', 'float']:
                        subimage_spec.attribute(image_in.nativespec().extra_attribs[i].name,
                                                image_in.nativespec().extra_attribs[i].value)
                    else:
                        subimage_spec.attribute(image_in.nativespec().extra_attribs[i].name,
                                                image_in.nativespec().extra_attribs[i].type,
                                                image_in.nativespec().extra_attribs[i].value)
            if properties.get('compression'):
                subimage_spec.attribute('compression', properties['compression'].strip("'"))
            else:
                subimage_spec.attribute('compression', image_in.nativespec().getattribute('compression'))
            src_channel_names = image_in.nativespec().channelnames[properties['sub_start']:properties['sub_end'] + 1]
            if properties.get('fix_channels'):
                dst_channel_names = []
                for channel_name in src_channel_names:
                    if 'depth.z' in channel_name:
                        print('Correcting channel name: {}'.format(channel_name))
                        channel_name = 'depth.Z'
                    dst_channel_names.append(channel_name)
            else:
                dst_channel_names = src_channel_names
            subimage_spec.channelnames = dst_channel_names
            subimage_spec.attribute('name', properties['recent_sub'])
            properties['sub_names'].append(properties['recent_sub'])
            properties['sub_specs'].append(subimage_spec)
            out_buffer = oiio.ImageBufAlgo.channels(image_in, tuple(src_channel_names))
            out_buffer = oiio.ImageBufAlgo.cut(out_buffer, properties['roi'])
            properties['sub_pixels'].append(out_buffer)
            properties['sub_ch_count'] = 0
        channelindex += 1
        properties['recent_sub'] = properties['current_sub']
        properties['sub_ch_count'] += 1
    if len(properties['sub_specs']) != len(properties['sub_pixels']):
        print('Internal error. Mismatch between subimage specs and pixel data.')
        return
    return properties


def rewrap(src, dst, autocrop=False, multipart=False, rm_manifest=False, fix_channels=False, compression=None,
           verbose=False, *args, **kwargs):
    """
    :param src: source image
    :param dst: destination image
    :param autocrop: set data window to non-empty pixel in rgb
    :param multipart: split subimages
    :param rm_manifest: prune exr metadata
    :param fix_channels: make channels names Nuke digestible (e.g. depth.Z)
    :param compression: change compression, keeps the current one if None given
    :param verbose: write verbose information to the terminal
    :return: boolean if conversion was successful
    """
    def update_specs(spec, properties):
        spec.roi = properties['roi']
        if properties['rm_manifest']:
            for attribute in spec.extra_attribs:
                if 'manifest' in attribute.name:
                    try:
                        spec.erase_attribute(attribute.name)
                    except TypeError as e:
                        raise RuntimeWarning('Error while removing manifest')
        if properties['compression']:
            spec["Compression"] = compression.strip("'")
        return spec

    properties = locals()
    image_in = oiio.ImageBuf(src)
    properties['roi'] = image_in.roi
    if properties['autocrop']:
        properties['roi'] = oiio.ImageBufAlgo.nonzero_region(image_in, roi=properties['roi'])
    if properties['compression'] == 'keep':
        properties['compression'] = compression = None
    properties['recent_sub'] = ''
    properties['sub_ch_count'] = 0
    properties['sub_specs'] = []
    properties['sub_pixels'] = []
    properties['sub_names'] = []
    # set data window
    if verbose:
        print('Setting data window to: {roi}'.format(**properties))
        print('{n} channels found'.format(n=len(image_in.nativespec().channelnames)))
    properties['nsubimages'] = image_in.nsubimages
    if properties['nsubimages'] > 1:
        if properties.get('multipart', False):
            print('Input file {src} already has {nsubimages} subimages.'.format(**properties))
        properties['sub_specs'] = []
        properties['sub_pixels'] = []
        properties['sub_names'] = []
        for i in range(0, properties['nsubimages']):
            image_in = oiio.ImageBuf(src, i, 0)
            spec = image_in.nativespec()
            update_specs(spec, properties)
            properties['sub_specs'].append(spec)
            buffer = oiio.ImageBufAlgo.cut(image_in, properties['roi'])
            properties['sub_pixels'].append(buffer)
            properties['sub_names'].append(image_in.nativespec().getattribute('name'))
    elif properties.get('multipart', False):
        properties = split_subimages(image_in, properties)
    else:
        print('Writing single-part exr')
    image_out = oiio.ImageOutput.create(dst)
    if properties.get('sub_specs'):
        ok = image_out.open(dst, tuple(properties['sub_specs']))
        if not ok:
            print('OIIO error while opening {} for writing parts: {} '.format(dst, image_out.geterror()))
            return False
        i = 0
        for pixels in properties['sub_pixels']:
            if verbose:
                print('Writing subimage index {}: {}'.format(i, properties['sub_names'][i]))
            if i != 0:
                ok = image_out.open(dst, properties['sub_specs'][i], "AppendSubimage")
                if not ok:
                    print('OIIO error while appending subimage {}: {}'.format(dst, image_out.geterror()))
                    return False
            image_out.write_image(pixels.get_pixels())
            i += 1
    else:
        spec = image_in.nativespec()
        update_specs(spec, properties)
        ok = image_out.open(dst, spec)
        if not ok:
            print('OIIO error while opening {} for writing image: {}'.format(dst, image_out.geterror()))
            return False
        buffer = oiio.ImageBufAlgo.cut(image_in, properties['roi'])
        image_out.write_image(buffer.get_pixels())
    image_out.close()
    return True


def main(arguments):
    if not arguments['single_file']:
        arguments['input'] = detect_sequence(arguments['input'])
        if arguments.get('output'):
            arguments['output'] = detect_sequence(arguments['output'])
    input_path = arguments['input']
    output_path = arguments.get('output')
    overwrite = arguments.get('overwrite')
    dryrun = arguments.get('dryrun', False)
    framerange = arguments.get('framerange')
    if dryrun:
        print('Doing dry-run.')
    files = find_files(input_path, framerange)
    if not files:
        print('No files to process')
        return
    if not output_path:
        backup_dir, prev_backup = bu_dir(input_path)
    i = 0
    for image_file in files:
        if not os.path.isfile(image_file):
            print('{path} not found'.format(path=image_file))
            continue
        if output_path:
            src = image_file
            dst = dst_path(image_file, output_path, overwrite)
            if not dst:
                continue
        else:
            src = os.path.join(backup_dir, os.path.basename(image_file))
            dst = image_file
            if os.path.isfile(src):
                prev_backup = True
                print('Backup file for {filename} from previous conversion in place. Skipping...'.format(
                    filename=os.path.basename(src)))
                continue
        print('Re-wrapping {src} to {dst}'.format(src=src, dst=dst))
        if arguments.get('dryrun'):
            continue
        if not arguments.get('output'):
            if arguments.get('verbose'):
                print('Moving {orig} ---> {bak} for backup'.format(bak=src, orig=dst))
            shutil.move(dst, src)
        try:
            ok = rewrap(src, dst, **arguments)
        except Exception as e:
            if arguments.get('verbose'):
                traceback.print_exc()
            ok = False
        if not ok and not arguments.get('output'):
            print('Operation failed for {filename}, restoring backup file.'.format(
                filename=os.path.basename(dst)))
            shutil.move(src, dst)
        elif arguments.get('no_backup'):
            os.remove(src)
        i += 1
        progress = i * 100 / len(files)
        print("Progress: {}%".format(progress))
    if arguments.get('no_backup') and not prev_backup:
        try:
            os.removedirs(backup_dir)
        except:
            pass


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(
        description='Tool for processing OpenEXR image files to improve their usability in VFX workflows.'
                    'By default, the following operations will be performed:'
                    'Auto-Crop: Shrinks data window (= bounding box) to non-black pixels'
                    'Create multi-part exr: Splits channels into subimages based on their layer names'
                    'Fix channel names: Rename channel names which could cause conflicts in Nuke (depth.z to depth.Z)')
    p.add_argument("-v", "--verbose", action="store_true",
                   help=u"Verbose")
    p.add_argument("input", help=u"Input File")
    p.add_argument("-o", "--output",
                   help=u"Output File, if not specified the input file(s) will be overwritten while a backup"
                        u"is kept in the _BAK folder")
    p.add_argument("-F", "--framerange",
                   help=u"Framerange")
    p.add_argument("-a", "--autocrop", action="store_false",
                   help=u"Skip auto-crop")
    p.add_argument("-m", "--multipart", action="store_false",
                   help=u"Skip multi-part creation")
    p.add_argument("-f", "--fix_channels", action="store_false",
                   help=u"Skip channel name fix.")
    p.add_argument("-c", "--compression",
                   help=u"Override compression, if not specified the compression of the input image will be kept.")
    p.add_argument("-r", "--rm_manifest", action="store_true",
                   help=u"Remove cryptomatte manifests from metadata")
    p.add_argument("-s", "--single_file", action="store_true",
                   help=u"Skip sequence detection, only work on specified input file.")
    p.add_argument("-y", "--overwrite", action="store_true",
                   help=u"Overwrite output images that already exist")
    p.add_argument("-b", "--no_backup", action="store_true",
                   help=u"Don't keep backup of the original files (only relevant if no output specified")
    p.add_argument("-n", "--dryrun", action="store_true",
                   help=u"Dry run, prints out which images would be touched.")
    p.add_argument("-ui", "--user_interface", action="store_true",
                   help=u"Run graphical user interface")
    arguments = vars(p.parse_args())
    if arguments['user_interface']:
        import mainwindow
        mainwindow.main()
    else:
        if arguments['input'].split('.')[-1] in ['exr', 'EXR'] and not os.path.isdir(arguments['input']):
            main(arguments)
        else:
            print('Input must be an OpenEXR file or sequence.')
