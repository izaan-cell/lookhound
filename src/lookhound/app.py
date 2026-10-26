"""The Lookhound window: a centered home screen, and a results page that opens
when you search."""
import queue
import subprocess
import sys
import threading
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QFileInfo, QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import (
    QAbstractFileIconProvider, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter,
    QPainterPath, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QFileIconProvider,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import updater
from .brain import Brain
from .hunt import FILTERS, Hunter
from .scout import Scout
from .settings import ICON_CACHE, IS_MAC, IS_WIN, load_prefs, save_prefs
from .sniff import IMAGE_EXT

RESOURCES = Path(__file__).parent / "resources"

# Warm ivory paper, near-black ink, and the burnt orange from the logo.
LIGHT = {
    "bg": "#F7F1E6", "card": "#FFFDF9", "card_hover": "#F1ECE3", "ink": "#1D1B18",
    "muted": "#857D72", "line": "#E4DDD1", "accent": "#B86F30", "button": "#1D1B18",
    "button_ink": "#F7F1E6", "field": "#FFFDF9", "tile": "#EFE6D6",
}
DARK = {
    "bg": "#141311", "card": "#1C1A17", "card_hover": "#22201C", "ink": "#EEE8DE",
    "muted": "#9A9184", "line": "#2E2B26", "accent": "#E09A5C", "button": "#EEE8DE",
    "button_ink": "#141311", "field": "#1C1A17", "tile": "#25221E",
}
KIND_WORDS = {"image": "Picture", "document": "Writing", "code": "Code", "app": "App"}
CHIPS = [("all", "Everything"), ("image", "Pictures"), ("document", "Writing"),
         ("code", "Code"), ("app", "Apps")]
COLUMN = 620  # width of the centered home column
SERIF = "Instrument Serif"  # replaced with the loaded family name at startup


def stylesheet(c):
    return f"""
    QMainWindow, QDialog {{ background: {c['bg']}; }}
    QWidget {{ color: {c['ink']}; font-size: 14px; }}
    QLabel#hello {{ font-family: "{SERIF}"; font-size: 52px; }}
    QLabel#sub {{ color: {c['muted']}; font-size: 15px; }}
    QLabel#hint, QLabel#muted {{ color: {c['muted']}; font-size: 12px; }}
    QLabel#eyebrow {{ color: {c['accent']}; font-size: 11px; font-weight: 600; letter-spacing: 2px; }}
    QLineEdit#search {{
        background: {c['field']}; border: 1px solid {c['line']}; border-radius: 8px;
        padding: 13px 16px; font-size: 16px; selection-background-color: {c['accent']};
    }}
    QLineEdit#search:focus {{ border-color: {c['ink']}; }}
    QPushButton {{
        background: transparent; border: none; padding: 6px 2px; margin-right: 18px;
        color: {c['muted']}; font-weight: 500; border-bottom: 1px solid transparent;
    }}
    QPushButton:hover {{ color: {c['ink']}; }}
    QPushButton:checked {{ color: {c['ink']}; border-bottom: 1px solid {c['ink']}; }}
    QPushButton#go {{
        background: {c['button']}; color: {c['button_ink']}; border-radius: 8px;
        padding: 13px 24px; margin: 0; font-size: 15px; font-weight: 500;
    }}
    QPushButton#go:hover {{ background: {c['accent']}; }}
    QPushButton#quiet {{ margin: 0; }}
    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item, QListWidget::item:selected, QListWidget::item:hover {{
        background: transparent; border: none;
    }}
    QLabel#empty {{ color: {c['muted']}; font-family: "{SERIF}"; font-size: 24px; }}
    QWidget#divider {{ background: {c['line']}; }}
    QWidget#banner {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 8px; }}
    QWidget#imagechip {{ background: {c['field']}; border: 1px solid {c['line']}; border-radius: 8px; }}
    QListWidget#folders {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 6px; }}
    QDialog QPushButton {{ color: {c['ink']}; }}
    """


def reveal(path):
    if IS_MAC:
        subprocess.Popen(["open", "-R", path])
    elif IS_WIN:
        subprocess.Popen(["explorer", "/select,", path])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])


def rounded(pix, size, radius):
    """Scale a picture into a size by size tile with soft corners."""
    pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(clip)
    painter.drawPixmap((size - pix.width()) // 2, (size - pix.height()) // 2, pix)
    painter.end()
    return out


def search_field(placeholder):
    field = QLineEdit()
    field.setObjectName("search")
    field.setPlaceholderText(placeholder)
    field.setClearButtonEnabled(True)
    return field


class IconMaker(QObject):
    """App icons come from Qt's icon provider, which only works on the GUI thread.
    The indexer asks through a signal and waits for the answer."""

    asked = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.provider = QFileIconProvider()
        self.provider.setOptions(QAbstractFileIconProvider.Option.DontUseCustomDirectoryIcons)
        self.asked.connect(self._make)

    @Slot(str, object)
    def _make(self, path, reply):
        try:
            pix = self.provider.icon(QFileInfo(path)).pixmap(QSize(256, 256))
            if not pix.isNull():
                out = ICON_CACHE / (str(abs(hash(path))) + ".png")
                pix.save(str(out), "PNG")
                reply["file"] = out
        finally:
            reply["done"].set()

    def icon_for(self, path):
        reply = {"done": threading.Event()}
        if QThread.currentThread() == QApplication.instance().thread():
            self._make(str(path), reply)  # already on the GUI thread (a dropped app)
        else:
            self.asked.emit(str(path), reply)
            reply["done"].wait(10)
        return reply.get("file")


class Searcher(QObject):
    """Runs searches off the GUI thread; only the newest query matters."""

    results = Signal(list, int)

    def __init__(self, brain):
        super().__init__()
        self.brain = brain
        self._q = queue.Queue()
        threading.Thread(target=self._run, daemon=True, name="search").start()

    def ask(self, ticket, text, image, kinds):
        self._q.put((ticket, text, image, kinds))

    def _run(self):
        hunter = Hunter(self.brain)
        while True:
            job = self._q.get()
            while not self._q.empty():  # skip queries the user already typed past
                job = self._q.get()
            ticket, text, image, kinds = job
            try:
                found = hunter.search(text, image, kinds)
            except Exception as exc:
                print(f"[lookhound] search failed: {exc}")
                found = []
            self.results.emit(found, ticket)


class ResultCard(QWidget):
    """One result, laid out like an entry in a catalogue: picture, title,
    where it lives, a line of its contents, and a small-caps label."""

    def __init__(self, result, colors):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"ResultCard {{ background: transparent; border-bottom: 1px solid {colors['line']}; }}"
            f"ResultCard:hover {{ background: {colors['card_hover']}; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 14, 14, 14)
        lay.setSpacing(18)

        tile = QLabel()
        tile.setFixedSize(60, 60)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview = result.get("preview")
        pix = QPixmap(preview) if result["kind"] in ("image", "app") and preview else QPixmap()
        if not pix.isNull() and result["kind"] == "image":
            tile.setPixmap(rounded(pix, 60, 6))
        elif not pix.isNull():
            tile.setPixmap(pix.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        else:
            ext = Path(result["path"]).suffix.lstrip(".").lower()[:4] or "file"
            tile.setText(ext)
            tile.setStyleSheet(f"background: {colors['tile']}; color: {colors['ink']};"
                               f"border-radius: 6px; font-family: '{SERIF}'; font-style: italic;"
                               "font-size: 20px;")
        lay.addWidget(tile)

        text = QVBoxLayout()
        text.setSpacing(3)
        name = QLabel(Path(result["name"]).stem if result["kind"] == "app" else result["name"])
        name.setStyleSheet("font-weight: 500; font-size: 15px; background: transparent;")
        text.addWidget(name)
        folder = Path(result["path"]).parent
        where = QLabel(f"{folder.name or folder}")
        where.setObjectName("muted")
        text.addWidget(where)
        if result["kind"] in ("document", "code") and preview:
            peek = QLabel(" ".join(preview.split())[:160])
            peek.setObjectName("muted")
            peek.setMinimumWidth(1)  # let the row shrink instead of the preview widening it
            peek.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text.addWidget(peek)
        lay.addLayout(text, 1)

        label = QLabel(KIND_WORDS[result["kind"]].upper())
        label.setObjectName("eyebrow")
        lay.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)


class FoldersDialog(QDialog):
    def __init__(self, folders, parent):
        super().__init__(parent)
        self.setWindowTitle("Where should Lookhound look?")
        self.resize(540, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        lead = QLabel("Lookhound keeps an eye on these places:")
        lead.setStyleSheet("font-weight: 600;")
        lay.addWidget(lead)
        self.list = QListWidget()
        self.list.setObjectName("folders")
        self.list.addItems(folders)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add a folder")
        add.clicked.connect(self._add)
        remove = QPushButton("Stop looking here")
        remove.clicked.connect(lambda: [self.list.takeItem(self.list.row(i))
                                        for i in self.list.selectedItems()])
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _add(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder", str(Path.home()))
        if folder and not self.list.findItems(folder, Qt.MatchFlag.MatchExactly):
            self.list.addItem(folder)

    def folders(self):
        return [self.list.item(i).text() for i in range(self.list.count())]


class Window(QMainWindow):
    update_found = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lookhound")
        self.resize(820, 680)
        self.setMinimumSize(560, 480)
        self.setAcceptDrops(True)
        self.dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        self.colors = DARK if self.dark else LIGHT
        self.setStyleSheet(stylesheet(self.colors))

        self.prefs = load_prefs()
        self.brain = Brain()
        self.icons = IconMaker()
        self.scout = Scout(self.brain, self.prefs["folders"], self.icons.icon_for)
        self.searcher = Searcher(self.brain)
        self.ticket = 0
        self.image = None
        self.kinds = None

        self._build()
        self.scout.status.connect(self.status.setText)
        self.scout.count_changed.connect(
            lambda n: self.count.setText(f"{n:,} files indexed"))
        self.searcher.results.connect(self._show_results)
        self.update_found.connect(self._show_update)
        self.debounce = QTimer(self, singleShot=True, interval=280)
        self.debounce.timeout.connect(self._search)
        self.query.textChanged.connect(lambda: self.debounce.start())

        self.scout.start()
        threading.Thread(target=self._check_update, daemon=True).start()

    # ---- layout ----------------------------------------------------------

    def _build(self):
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(32, 24, 32, 16)
        lay.setSpacing(12)

        self.banner = QWidget()
        self.banner.setObjectName("banner")
        self.banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        b = QHBoxLayout(self.banner)
        b.setContentsMargins(18, 8, 8, 8)
        self.banner_text = QLabel()
        self.banner_button = QPushButton("Update")
        self.banner_button.setObjectName("go")
        b.addWidget(self.banner_text, 1)
        b.addWidget(self.banner_button)
        self.banner.hide()
        lay.addWidget(self.banner)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._home_page())
        self.pages.addWidget(self._results_page())
        lay.addWidget(self.pages, 1)

        foot = QHBoxLayout()
        self.status = QLabel("Starting…")
        self.status.setObjectName("muted")
        self.count = QLabel("")
        self.count.setObjectName("muted")
        folders = QPushButton("Folders")
        folders.setObjectName("quiet")
        folders.setCursor(Qt.CursorShape.PointingHandCursor)
        folders.clicked.connect(self._edit_folders)
        foot.addWidget(self.status, 1)
        foot.addWidget(self.count)
        foot.addWidget(folders)
        lay.addLayout(foot)

        self.setCentralWidget(root)

    def _home_page(self):
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.addStretch()
        col = QVBoxLayout()
        col.setSpacing(14)
        col.addStretch(3)

        dog = QLabel()
        dog.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dog_pix = QPixmap(str(RESOURCES / "lookhound-illustration.png"))
        if not dog_pix.isNull():
            ratio = self.devicePixelRatioF()
            dog_pix = dog_pix.scaledToHeight(int(150 * ratio), Qt.TransformationMode.SmoothTransformation)
            dog_pix.setDevicePixelRatio(ratio)
            dog.setPixmap(dog_pix)
            if self.dark:  # black ink vanishes on dark, so give the dog a paper card
                dog.setStyleSheet("background: #EFE6D6; border-radius: 22px; padding: 14px;")
                dog.setFixedWidth(int(dog_pix.width() / ratio) + 28)
        dog_row = QHBoxLayout()
        dog_row.addStretch()
        dog_row.addWidget(dog)
        dog_row.addStretch()
        col.addLayout(dog_row)

        eyebrow = QLabel("LOOKHOUND")
        eyebrow.setObjectName("eyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(eyebrow)

        hello = QLabel("What are you <i>looking</i> for?")
        hello.setObjectName("hello")
        hello.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("Describe a file in your own words, or show it a picture.")
        sub.setObjectName("sub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(hello)
        col.addWidget(sub)
        col.addSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.home_query = search_field("the blue logo, that school trip PDF, my login code…")
        self.home_query.returnPressed.connect(self._go)
        go = QPushButton("Search")
        go.setObjectName("go")
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.clicked.connect(self._go)
        row.addWidget(self.home_query, 1)
        row.addWidget(go)
        col.addLayout(row)

        hint = QLabel("Drop an image anywhere on this window to find ones like it.")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(hint)
        col.addStretch(4)

        holder = QWidget()
        holder.setLayout(col)
        holder.setMaximumWidth(COLUMN)
        holder.setMinimumWidth(min(COLUMN, 460))
        outer.addWidget(holder, 1)
        outer.addStretch()
        return page

    def _results_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        back = QPushButton("←  Back")
        back.setObjectName("quiet")
        back.setStyleSheet("color: palette(text);")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self._home)
        bar.addWidget(back)
        self.query = search_field("Describe what you're looking for…")
        bar.addWidget(self.query, 1)

        self.image_chip = QWidget()
        self.image_chip.setObjectName("imagechip")
        self.image_chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        chip = QHBoxLayout(self.image_chip)
        chip.setContentsMargins(6, 6, 4, 6)
        self.image_thumb = QLabel()
        self.image_thumb.setFixedSize(40, 40)
        clear = QPushButton("✕")
        clear.setObjectName("quiet")
        clear.setToolTip("Forget this picture")
        clear.clicked.connect(self._clear_image)
        chip.addWidget(self.image_thumb)
        chip.addWidget(clear)
        self.image_chip.hide()
        bar.addWidget(self.image_chip)
        lay.addLayout(bar)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip_group = QButtonGroup(self)
        for key, label in CHIPS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self.chip_group.addButton(btn)
            chips.addWidget(btn)
        chips.addStretch()
        lay.addLayout(chips)

        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(lambda item: reveal(item.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.list, 1)

        self.empty = QLabel()
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.hide()
        lay.addWidget(self.empty, 1)
        return page

    # ---- moving between pages ----------------------------------------------

    def _go(self):
        text = self.home_query.text().strip()
        if not text and self.image is None:
            return
        self.query.blockSignals(True)
        self.query.setText(text)
        self.query.blockSignals(False)
        self.pages.setCurrentIndex(1)
        self.query.setFocus()
        self._search()

    def _home(self):
        self.image = None
        self.image_chip.hide()
        self.home_query.clear()
        self.list.clear()
        self.pages.setCurrentIndex(0)
        self.home_query.setFocus()

    def _say(self, text):
        self.list.hide()
        self.empty.setText(text)
        self.empty.show()

    # ---- searching -------------------------------------------------------

    def _set_filter(self, key):
        self.kinds = FILTERS[key]
        self._search()

    def _search(self):
        if self.pages.currentIndex() != 1:
            return
        text = self.query.text()
        if not text.strip() and self.image is None:
            self.list.clear()
            self._say("Type something to look for.")
            return
        self.ticket += 1
        self._say("Searching…")
        self.searcher.ask(self.ticket, text, self.image, self.kinds)

    @Slot(list, int)
    def _show_results(self, results, ticket):
        if ticket != self.ticket:
            return
        self.list.clear()
        if not results:
            self._say("Nothing yet. Try describing it another way.")
            return
        self.empty.hide()
        self.list.show()
        place = "Finder" if IS_MAC else "File Explorer"
        for r in results:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r["path"])
            item.setToolTip(f"Click to show it in {place}\n{r['path']}")
            card = ResultCard(r, self.colors)
            item.setSizeHint(card.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, card)

    # ---- image queries ---------------------------------------------------

    def _image_from_drop(self, mime):
        for url in mime.urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in IMAGE_EXT or path.suffix.lower() in (".app", ".icns"):
                return path
        return None

    def dragEnterEvent(self, event):
        if self._image_from_drop(event.mimeData()) or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event):
        mime = event.mimeData()
        path = self._image_from_drop(mime)
        if path and path.suffix.lower() == ".app":
            path = self.icons.icon_for(path)
        if path:
            image = Image.open(path).convert("RGB")
            pix = QPixmap(str(path))
        elif mime.hasImage():
            qimg = mime.imageData()
            tmp = ICON_CACHE / "dropped.png"
            qimg.save(str(tmp))
            image = Image.open(tmp).convert("RGB")
            pix = QPixmap.fromImage(qimg)
        else:
            return
        self.image = image
        self.image_thumb.setPixmap(rounded(pix, 40, 12))
        self.image_chip.show()
        if self.pages.currentIndex() == 0:
            self._go()
        else:
            self._search()

    def _clear_image(self):
        self.image = None
        self.image_chip.hide()
        self._search()

    # ---- folders and updates ---------------------------------------------

    def _edit_folders(self):
        dialog = FoldersDialog(self.prefs["folders"], self)
        if dialog.exec():
            self.prefs["folders"] = dialog.folders()
            save_prefs(self.prefs)
            self.scout.set_folders(self.prefs["folders"])

    def _check_update(self):
        try:
            found = updater.check()
        except Exception:
            found = None
        if found:
            self.update_found.emit(*found)

    @Slot(str, str)
    def _show_update(self, version, url):
        self.banner_text.setText(f"Lookhound {version} is available.")
        self.banner_button.clicked.connect(lambda: (self.banner_text.setText(
            "Downloading…"), QTimer.singleShot(50, lambda: updater.install(url))))
        self.banner.show()

    def closeEvent(self, event):
        self.scout.stop()
        super().closeEvent(event)


def load_font(app):
    """Geist for text, Instrument Serif for headlines, both bundled."""
    global SERIF
    fonts = RESOURCES / "fonts"
    for name in ("InstrumentSerif-Regular.ttf", "InstrumentSerif-Italic.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(fonts / name))
        if font_id != -1:
            SERIF = QFontDatabase.applicationFontFamilies(font_id)[0]
    font_id = QFontDatabase.addApplicationFont(str(fonts / "Geist.ttf"))
    if font_id != -1:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 13))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Lookhound")
    load_font(app)
    icon = RESOURCES / "lookhound.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = Window()
    window.show()
    sys.exit(app.exec())
