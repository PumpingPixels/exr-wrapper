# exr-wrapper

## Overview

Python script for processing OpenEXR image files to improve their usability in VFX workflows. It uses the Python
bindings of the [OpenImageIO library](https://sites.google.com/site/openimageio/home) which has to be installed and
accessible to the Python interpreter, e.g. using the PYTHONPATH environment variable.

The following processing operations are available:

* Auto-Crop: Shrinks data window (= bounding box) to non-black pixel
* Create multi-part exr: Splits channels into subimages based on their layer names
* Fix channel names: Rename channel names which could cause conflicts in Nuke (depth.z to depth.Z)
* Remove cryptomatte manifests from metadata to decrease file-size and improve file-IO
* Change compression mode

## Usage

### Command line:

`python src/wrapper.py /path/to/image.1001.exr` will automatically detect the full image sequence and replace the images
after applying the following operations: Auto-Crop, multi-part rewrap and channel name fix. A backup of the original
files will be kept in a _BAK subfolder of the original location. For additional information, look into the --help
option.

### GUI:

`python src/mainwindow.py` launches a minimal UI which offers the same options like the CLI interface.

![GUI-Layout](/docs/gui.png)

## Requirements

* [OpenImageIO](https://github.com/OpenImageIO/oiio/blob/master/INSTALL.md)
* numpy
* PySide2 (for GUI-usage only)