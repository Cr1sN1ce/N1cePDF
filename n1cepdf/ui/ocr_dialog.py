"""Native OCR controls; workers report progress through queued Qt signals."""
import threading
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QLineEdit, QCheckBox,
                               QLabel, QDialogButtonBox, QMessageBox, QProgressDialog)
from ..core.ocr import recognize_pdf, OcrCancelled
from ..edit.objects import TextBox, bbox
from ..edit.commands import AddObject, RemoveObject
from ..edit.scan_rebuild import changed
from ..edit.undo import MacroCommand


def parse_pages(value, count):
    selected = set()
    for part in value.split(','):
        ends = part.strip().split('-')
        if len(ends) not in (1, 2):
            raise ValueError('Use page numbers or ranges, for example 1,3-5.')
        first, last = int(ends[0]), int(ends[-1])
        if not 1 <= first <= last <= count:
            raise ValueError(f'Pages must be between 1 and {count}.')
        selected.update(range(first-1, last))
    return sorted(selected)


class Progress(QObject):
    changed = Signal(int, int)


class OcrDialog(QDialog):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.setWindowTitle('Распознать текст (OCR)')
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language = QComboBox()
        for label, code in [('English', 'eng'), ('Русский', 'rus'), ('Română', 'ron'),
                            ('English + Русский + Română', 'eng+rus+ron')]:
            self.language.addItem(label, code)
        form.addRow('Язык', self.language)
        self.scope = QComboBox()
        self.scope.addItems(['Текущая страница', 'Выбранные страницы', 'Весь документ',
                             'Автоматически: страницы без текста'])
        form.addRow('Область', self.scope)
        self.pages = QLineEdit(str(tab.view.current_page+1))
        form.addRow('Страницы (1,3-5)', self.pages)
        layout.addLayout(form)
        self.replace_previous = QCheckBox("Заменить прежние неизменённые OCR-блоки (новая сегментация)")
        self.replace_previous.setToolTip("Пересоздаёт выбранные неизменённые OCR-блоки с координатами слов. Изменённые блоки сохраняются; операция отменяется через Undo.")
        layout.addWidget(self.replace_previous)
        self._replace_manual=False
        self.replace_previous.clicked.connect(lambda _: setattr(self,"_replace_manual",True))
        self.resegment_hint=QLabel()
        self.resegment_hint.setWordWrap(True)
        layout.addWidget(self.resegment_hint)
        self.scope.currentIndexChanged.connect(self._refresh_resegment_hint)
        self.pages.textChanged.connect(self._refresh_resegment_hint)
        self._refresh_resegment_hint()
        note = QLabel('OCR добавляет невидимый текст и рамки строк. Выберите строку инструментом '
                      'редактирования текста и измените её в панели свойств. Исходная область '
                      'изменяется в пикселях исходного изображения с подбором шрифта и локального цвета фона. '
                      'Сложный фон требует включить inpainting в Properties и проверить возможные артефакты. '
                      'Растеризация всей страницы разрешается отдельно: она удаляет векторные элементы '
                      'только затронутой страницы. Это не защищённое удаление данных. Отмена OCR не применяет частичные результаты.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.start)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _old_geometry(obj):
        if not isinstance(obj,TextBox) or obj.ocr_source is None:
            return False
        if not obj.ocr_words:
            return True
        if len(obj.ocr_regions)>1:
            return False  # an intentional manual merge keeps separate masks
        from ..core.ocr_layout import OcrWord,segment_rows
        return len(segment_rows([[OcrWord(text,rect) for text,rect in obj.ocr_words]]))>1

    def _selected_pages(self):
        scope=self.scope.currentIndex()
        return (parse_pages(self.pages.text(),self.tab.doc.page_count) if scope==1 else
                [self.tab.view.current_page] if scope==0 else list(range(self.tab.doc.page_count)))

    def _refresh_resegment_hint(self,*_):
        try:
            pages=self._selected_pages()
        except ValueError:
            return
        old=[obj for index in pages for obj in self.tab.session.layer.on_page(self.tab.doc.page_key(index))
             if self._old_geometry(obj)]
        repairable=[obj for obj in old if not changed(obj)]
        protected=[obj for obj in old if changed(obj)]
        if not self._replace_manual:
            self.replace_previous.setChecked(bool(repairable))
        messages=[]
        if repairable:
            messages.append(f"Прежних блоков со старой общей геометрией: {len(repairable)}. Включена замена неизменённых блоков новой сегментацией; её можно отменить через Undo.")
        if protected:
            messages.append(f"Изменённых прежних блоков: {len(protected)}. Они сохранены и не будут удалены; общая рамка останется. Для разделения сначала отмените их изменения либо сохраните копию, откройте её заново и выполните OCR.")
        self.resegment_hint.setText("\n".join(messages))
        self.resegment_hint.setVisible(bool(messages))

    def start(self):
        tab = self.tab
        if not tab._can_edit():
            return
        scope = self.scope.currentIndex()
        try:
            pages = self._selected_pages()
        except ValueError as exc:
            QMessageBox.warning(self, 'OCR', str(exc))
            return
        revision = tab.doc.revision
        keys = tab.doc.page_keys()
        excluded = {}
        replaced = []
        for i, key in enumerate(keys):
            excluded[i] = []
            for obj in tab.session.layer.on_page(key):
                if (i in pages and self.replace_previous.isChecked() and isinstance(obj,TextBox)
                        and obj.ocr_source is not None and not changed(obj)):
                    replaced.append((i,obj))
                    continue
                excluded[i].append(bbox(obj))
                if getattr(obj, "ocr_source", None) is not None:
                    excluded[i].append(obj.ocr_source)
        protected_legacy=[obj for i in pages for obj in tab.session.layer.on_page(keys[i])
                          if self._old_geometry(obj) and changed(obj)]
        cancel = threading.Event()
        progress = QProgressDialog('Распознавание текста…', 'Отмена', 0, len(pages), tab)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        signals = Progress(progress)
        signals.changed.connect(lambda done, total: progress.setValue(done))
        progress.canceled.connect(cancel.set)
        language = self.language.currentData()
        tab.busy += 1
        tab.state_changed.emit()

        def work(shutdown):
            # Closing the tab cancels the subprocess too.
            class Cancellation:
                def is_set(self): return cancel.is_set() or shutdown.is_set()
                def wait(self, timeout): return shutdown.wait(timeout) or cancel.is_set()
            with tab.doc_lock:
                data = tab.doc.snapshot_bytes()
            return recognize_pdf(data, pages, language, Cancellation(), signals.changed.emit,
                                 automatic=scope == 3, excluded=excluded)

        def finish():
            progress.blockSignals(True)
            progress.close()
            progress.deleteLater()
            tab.busy -= 1
            tab.state_changed.emit()

        def done(lines):
            finish()
            if cancel.is_set():
                return
            if tab.doc.revision != revision:
                QMessageBox.warning(tab, 'OCR', 'Документ изменился. Запустите OCR повторно.')
                return
            successful_pages={line.page for line in lines}
            objects = [RemoveObject(tab.session,obj.id,"Replace OCR segmentation")
                       for index,obj in replaced if index in successful_pages]
            count = 0
            for line in lines:
                l, b, r, t = line.rect
                # A little space below the glyphs gives the existing text editor room for its baseline.
                rect = (l, b-line.size*0.3, r+1, t)
                match = line.match
                family = match.family if match else tab.controller.options.text_family
                size = match.size if match else line.size
                if match:
                    from ..edit.fonts import metrics
                    face = tab.session.catalog.find(family,match.bold,match.italic)
                    asc,_ = metrics(face)
                    # Matching samples use recognition resolution (bounded for large pages).
                    crop = tab.doc.page_crop_box(line.page)
                    sample_scale = min(300/72,8000/max(crop[2]-crop[0],crop[3]-crop[1]))
                    baseline = t-match.baseline/sample_scale
                    top = baseline+asc*size
                    rect = (l-match.left_bearing,min(b-size*0.3,top-size*1.5),r+1,top)
                if line.align=='right':
                    rect=(rect[0],rect[1],r,rect[3])
                obj = TextBox(keys[line.page], *rect, text=line.text, size=size,
                              family=family, origin='ocr',
                              bold=match.bold if match else False, italic=match.italic if match else False,
                              color=match.color if match else (0,0,0),
                              char_spacing=match.char_spacing if match else 0,
                              align=line.align,
                              ocr_allow_page_raster=tab.session.page_raster_allowed(keys[line.page]),
                              ocr_words=tuple((word.text,word.rect) for word in line.words),
                              ocr_source=(l-0.5, b-0.5, r+0.5, t+0.5),
                              ocr_original=line.text, ocr_initial_rect=rect)
                objects.append(AddObject(tab.session, obj, 'OCR'))
                count += 1
            if objects:
                tab.session.push(MacroCommand('Распознать текст (OCR)', objects))
                tab.controller.set_tool('edit_text')
            message=f'OCR: {count} блоков. Выберите блок для редактирования.'
            if protected_legacy:
                message+=f' Сохранено изменённых прежних блоков: {len(protected_legacy)}; их общая рамка не пересегментирована.'
            tab.message.emit(message)

        def failed(exc):
            finish()
            if not isinstance(exc, OcrCancelled):
                QMessageBox.warning(tab, 'Ошибка OCR', str(exc))

        tab.submit(work, done, failed)
        self.accept()
        progress.show()
