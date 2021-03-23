import os
import shutil

from PySide2 import QtCore, QtWidgets, QtUiTools

class DropWidget(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super(DropWidget, self).__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            line_edit = self.findChild(QtWidgets.QLineEdit)
            line_edit.setText(path)
            line_edit.editingFinished.emit()


class WrapperThread(QtCore.QThread):
    progress = QtCore.Signal(float)
    message = QtCore.Signal(str)

    def __init__(self, parent):
        super(WrapperThread, self).__init__(parent)

    def set_properties(self, properties):
        self.properties = properties

    def cancel(self):
        self.canceled = True

    def run(self):
        from wrapper import find_files, bu_dir, dst_path, rewrap
        self.canceled = False
        kwargs = self.properties
        files = find_files(kwargs['input'], kwargs['framerange'])
        if not kwargs.get('output'):
            backup_dir, prev_backup = bu_dir(kwargs['input'])
        i = 0
        for image_file in files:
            if self.canceled:
                break
            if not os.path.isfile(image_file):
                self.message.emit('{path} not found'.format(path=image_file))
                continue
            if kwargs['output']:
                src = image_file
                dst = dst_path(image_file, kwargs['output'], kwargs['overwrite'])
                if not dst:
                    continue
            else:
                src = os.path.join(backup_dir, os.path.basename(image_file))
                dst = image_file
                if os.path.isfile(src):
                    prev_backup = True
                    self.message.emit('Backup file from previous conversion in place. Process canceled.'.format(
                        filename=os.path.basename(src)))
                    return
            self.message.emit(os.path.basename(dst))
            if not kwargs['output']:
                shutil.move(dst, src)
            import traceback
            try:
                ok = rewrap(src, dst, **kwargs)
            except Exception as e:
                traceback.print_exc()
                ok = False
            if not ok and not kwargs.get('output'):
                self.message.emit('Operation failed for {filename}, restoring backup file.'.format(
                    filename=os.path.basename(dst)))
                shutil.move(src, dst)
            elif kwargs.get('no_backup'):
                os.remove(src)
            i += 1
            progress = i * 100.0 / len(files)
            self.progress.emit(progress)
        if kwargs.get('no_backup') and not prev_backup:
            try:
                os.removedirs(backup_dir)
            except OSError:
                pass
        if self.canceled:
            self.message.emit('Canceled')
        else:
            self.message.emit('Finished')


class Manager(QtCore.QObject):
    def __init__(self, parent_widget=None, parent=None):
        super(Manager, self).__init__(parent)
        loader = QtUiTools.QUiLoader()
        loader.registerCustomWidget(DropWidget)
        file = QtCore.QFile("../ui/mainwindow.ui")
        file.open(QtCore.QFile.ReadOnly)
        self.window = loader.load(file, parent_widget)
        file.close()
        self.window.installEventFilter(self)
        self.window.show()
        self.setParent(self.window)
        self.window.lineEdit_input1.editingFinished.connect(self.detect_sequence)
        self.window.lineEdit_output.editingFinished.connect(self.detect_sequence)
        self.window.pushButton_rewrap.clicked.connect(self.run)
        self.window.pushButton_cancel.clicked.connect(self.cancel)
        self.window.pushButton_browse_input1.clicked.connect(self.file_dialog)
        self.window.pushButton_browse_output.clicked.connect(self.file_dialog)
        self.window.setAcceptDrops(True)
        self.thread = WrapperThread(self.window)
        self.thread.message.connect(self.message)
        self.thread.progress.connect(self.progress)

    def cancel(self):
        if self.thread.isRunning():
            self.thread.cancel()
        else:
            self.window.close()

    def progress(self, progress):
        self.window.progressBar.setValue(progress)

    def message(self, message):
        self.window.plainTextEdit_log.appendPlainText(message)

    def detect_sequence(self, line_edit=None):
        from wrapper import find_sequence
        if not line_edit:
            line_edit = self.sender()
        path = line_edit.text()
        if path.split('.')[-1] in ['exr', 'EXR'] and not os.path.isdir(path):
            path, first, last = find_sequence(path)
            line_edit.setText(path)
            if 'input' in line_edit.objectName():
                self.window.pushButton_rewrap.setEnabled(bool(path))
                if first and last:
                    self.window.findChild(QtWidgets.QSpinBox, 'spinBox_first').setValue(first)
                    self.window.findChild(QtWidgets.QSpinBox, 'spinBox_last').setValue(last)
                else:
                    self.window.findChild(QtWidgets.QSpinBox, 'spinBox_first').setValue(0)
                    self.window.findChild(QtWidgets.QSpinBox, 'spinBox_last').setValue(0)
                self.window.progressBar.setValue(0)
            elif 'output' in line_edit.objectName():
                self.window.checkBox_keep_backup.setEnabled(not bool(path))
                self.window.checkBox_overwrite.setEnabled(bool(path))
        else:
            line_edit.clear()
            self.window.plainTextEdit_log.appendPlainText('Input must be an OpenEXR file or sequence.')


    def file_dialog(self):
        line_edit = self.sender().parent().findChild(QtWidgets.QLineEdit)
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.window,
            "Choose sequence ...",
            os.path.dirname(line_edit.text()),
            "OpenEXR Files (*.exr)",
        )
        if file_name:
            line_edit.setText(file_name)
            line_edit.editingFinished.emit()

    def run(self):
        self.window.plainTextEdit_log.clear()
        self.window.pushButton_rewrap.setEnabled(False)
        properties = {}
        properties['input'] = self.window.lineEdit_input1.text()
        properties['output'] = self.window.lineEdit_output.text()
        properties['multipart'] = self.window.checkBox_multipart.isChecked()
        properties['autocrop'] = self.window.checkBox_autocrop.isChecked()
        properties['fix_channels'] = self.window.checkBox_fix_channels.isChecked()
        properties['ex_manifest'] = self.window.checkBox_ex_manifest.isChecked()
        properties['no_backup'] = not self.window.checkBox_keep_backup.isChecked()
        properties['overwrite'] = self.window.checkBox_overwrite.isChecked()
        properties['compression'] = self.window.comboBox_compression.currentText()
        first = self.window.spinBox_first.value()
        last = self.window.spinBox_last.value()
        properties['framerange'] = '{}-{}'.format(first, last)
        if properties['input'] == properties['output']:
            properties.pop('output')
        self.thread.set_properties(properties)
        self.thread.start()


def main():
    import sys
    app = QtWidgets.QApplication(sys.argv)
    test = Manager()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
