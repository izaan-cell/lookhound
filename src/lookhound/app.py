"""The Lookhound window: a centered home screen, and a results page that opens
when you search."""
import os
import queue
import random
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QFileInfo, QObject, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import (
    QAbstractFileIconProvider, QAction, QDesktopServices, QFont, QFontDatabase, QIcon,
    QKeySequence, QPainter, QPainterPath, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QFileIconProvider,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import updater
from .brain import Brain
from .hunt import FILTERS, Hunter
from .scout import Scout
from .shelf import Shelf
from .settings import ICON_CACHE, IS_MAC, IS_WIN, load_prefs, save_prefs
from .sniff import APP_EXT, ARCHIVE_EXT, CODE_EXT, DOC_EXT, IMAGE_EXT, sniff

LOOKALIKE_EXT = IMAGE_EXT | APP_EXT | {".icns"}  # searched by how they look
PICKABLE_EXT = LOOKALIKE_EXT | DOC_EXT | CODE_EXT | ARCHIVE_EXT  # what + and drops accept

RESOURCES = Path(__file__).parent / "resources"

# Always the warm dark brown, cream ink, and the burnt orange from the logo.
COLORS = {
    "bg": "#141311", "card": "#1C1A17", "card_hover": "#22201C", "ink": "#EEE8DE",
    "muted": "#9A9184", "line": "#2E2B26", "accent": "#E09A5C", "button": "#EEE8DE",
    "button_ink": "#141311", "field": "#1C1A17", "tile": "#25221E", "paper": "#EFE6D6",
    "alert": "#E8B84A",
}
KIND_WORDS = {"image": "Picture", "document": "Writing", "code": "Code", "app": "App",
              "archive": "Archive"}
CHIPS = [("all", "Everything"), ("image", "Pictures"), ("document", "Writing"),
         ("code", "Code"), ("app", "Apps"), ("archive", "Archives")]
COLUMN = 620  # width of the centered home column
SERIF = "Instrument Serif"  # replaced with the loaded family name at startup


def stylesheet(c):
    return f"""
    QMainWindow, QDialog {{ background: {c['bg']}; }}
    QWidget {{ color: {c['ink']}; font-size: 14px; }}
    QLabel#hello {{ font-family: "{SERIF}"; font-size: 52px; }}
    QLabel#sub {{ color: {c['muted']}; font-size: 15px; }}
    QLabel {{ selection-background-color: {c['accent']}; selection-color: {c['button_ink']}; }}
    QLabel#hint, QLabel#muted {{ color: {c['muted']}; font-size: 12px; }}
    QMenu {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {c['card_hover']}; color: {c['accent']}; }}
    QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 8px; }}
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
    QPushButton#plus {{
        margin: 0; padding: 0; min-width: 48px; max-width: 48px; min-height: 48px; max-height: 48px;
        background: {c['field']}; border: 1px solid {c['line']}; border-radius: 8px;
        color: {c['ink']}; font-size: 24px; font-weight: 300;
    }}
    QPushButton#plus:hover {{ border-color: {c['accent']}; color: {c['accent']}; }}
    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item, QListWidget::item:selected, QListWidget::item:hover {{
        background: transparent; border: none;
    }}
    QLabel#asked {{ font-family: "{SERIF}"; font-size: 30px; }}
    QLabel#empty {{ color: {c['muted']}; font-family: "{SERIF}"; font-size: 24px; }}
    QWidget#divider {{ background: {c['line']}; }}
    QWidget#banner {{ background: {c['alert']}; border-radius: 8px; }}
    QWidget#banner QLabel {{ color: {c['button_ink']}; font-size: 13px; }}
    QPushButton#bannerlink {{ margin: 0; padding: 8px 12px; color: {c['button_ink']}; font-weight: 600; }}
    QPushButton#bannerlink:hover {{ text-decoration: underline; }}
    QPushButton#bannergo {{
        margin: 0; padding: 8px 16px; border-radius: 6px; background: {c['button_ink']};
        color: {c['alert']}; font-weight: 600;
    }}
    QWidget#imagechip {{ background: {c['field']}; border: 1px solid {c['line']}; border-radius: 8px; }}
    QListWidget#folders {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 6px; }}
    QDialog QPushButton {{ color: {c['ink']}; }}
    QPushButton#greeting {{
        margin: 0; padding: 2px 6px; color: {c['accent']}; font-size: 11px; font-weight: 600;
        letter-spacing: 2px;
    }}
    QPushButton#greeting:hover {{ border-bottom: 1px solid {c['accent']}; }}
    QPushButton#recent {{
        margin: 0; padding: 4px 10px; border: 1px solid {c['line']}; border-radius: 13px;
        color: {c['ink']}; font-size: 12px;
    }}
    QPushButton#recent:hover {{ border-color: {c['accent']}; color: {c['accent']}; }}
    QPushButton#fresh {{
        margin: 0; padding: 7px 4px; color: {c['muted']}; font-size: 13px;
        border-bottom: 1px solid {c['line']};
    }}
    QPushButton#fresh:hover {{ color: {c['ink']}; }}
    """


# The home headline changes every time it's shown; one word in italics each.
HEADLINES = [
    "What can I <i>find</i> for you?",
    "What are we <i>looking</i> for?",
    "What went <i>missing</i> today?",
    "Where did it <i>wander</i> off?",
    "Tell me what you're <i>after</i>.",
    "What shall I <i>sniff</i> out?",
    "Lost something <i>lovely</i>?",
    "Let's go <i>fetch</i> it.",
    "What's on your <i>mind</i>?",
    "What are we <i>hunting</i> for?",
    "Point me to the <i>scent</i>.",
    "What's gone <i>astray</i>?",
    "Ready when <i>you</i> are.",
    "Let's track it <i>down</i>.",
    "Nose to the <i>ground</i>.",
    "Give me a <i>clue</i>.",
    "Who's a good <i>finder</i>?",
    "Tail wagging, <i>ready</i>.",
    "What did you <i>misplace</i>?",
    "I'll find it, <i>promise</i>.",
    "Say the word, I'll <i>fetch</i>.",
    "What are you <i>missing</i>?",
    "Hiding <i>somewhere</i>, is it?",
    "What did it <i>look</i> like?",
    "One whiff and it's <i>yours</i>.",
    "Where to <i>today</i>?",
    "On the <i>case</i>.",
    "Ears up. What's <i>lost</i>?",
    "Let's <i>dig</i> it up.",
    "Describe it. I'll <i>look</i>.",
    "Which file is <i>hiding</i>?",
    "Paws ready. <i>Go</i>.",
    "What's the <i>mystery</i>?",
    "Good to see <i>you</i>.",
    "Let's go <i>sniffing</i>.",
    "What's <i>slipped</i> away?",
    "Show me the <i>trail</i>.",
    "Need a <i>hand</i>? A paw?",
    "Ask, and I'll <i>retrieve</i>.",
    "Lost? Soon <i>found</i>.",
    "What's it <i>called</i> again?",
    "Somewhere in the <i>pile</i>?",
    "Buried in a <i>folder</i>?",
    "Where's that one <i>thing</i>?",
    "Let me <i>help</i> you look.",
    "Leave the <i>looking</i> to me.",
    "What's the <i>hunt</i> today?",
    "Sniffing <i>around</i> for you.",
    "Your files, <i>fetched</i>.",
    "What's <i>hiding</i> from you?",
    "Lead the <i>way</i>.",
    "I'm all <i>ears</i>.",
    "Fancy a <i>fetch</i>?",
]
RECENT_KEEP = 6
WAKING = ("Still fetching what I need to understand your files.\n"
          "The first time takes a few minutes; I'll search again by myself the moment I'm ready.")
NOTHING_FOUND = ("I couldn't find that one.\n"
                 "Try describing it another way, or check Folders to make sure I'm looking there.")


def first_name():
    """The person's first name from their account, or a gentle fallback."""
    name = ""
    try:
        if IS_WIN:
            import ctypes

            size = ctypes.c_ulong(256)
            buf = ctypes.create_unicode_buffer(256)
            if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)):  # display name
                name = buf.value
        else:
            import pwd

            name = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
    except Exception:
        pass
    if not name:
        import getpass

        name = getpass.getuser()
    first = name.replace(".", " ").split()[0] if name.strip() else ""
    return first[:1].upper() + first[1:]


def greet(name=None):
    """(greeting, comforting line) for the time of day. An empty saved name means
    the person asked not to be called anything."""
    if name is None:
        name = first_name()
    hour = datetime.now().hour
    if 5 <= hour < 12:
        part, comfort = "Good morning", "Take your time. Describe it however you like."
    elif 12 <= hour < 17:
        part, comfort = "Good afternoon", "Describe it in your own words, or show me a picture."
    elif 17 <= hour < 22:
        part, comfort = "Good evening", "Describe it in your own words, or show me a picture."
    else:
        part, comfort = "Still up", "Let's find it quickly, then you can rest."
    return (f"{part}, {name}" if name else part), comfort


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


SITE = "https://lookhound.spools.studio"
MIN_HEIGHT = 740  # tall enough for the home screen; the menu bar and update bar add to it


def changelog_url(version=None):
    """The website's changelog, or one version's full report (/changelog/v0-3-0)."""
    if version is None:
        return f"{SITE}/changelog"
    return f"{SITE}/changelog/v{version.replace('.', '-')}"


def selectable(label):
    """Let a label's text be highlighted with the mouse and copied with Cmd/Ctrl+C.
    Right-clicks pass through to the results menu instead of Qt's own."""
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                  | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    label.setCursor(Qt.CursorShape.IBeamCursor)
    label.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    return label


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


def plus_button(on_click):
    plus = QPushButton("+")
    plus.setObjectName("plus")
    plus.setCursor(Qt.CursorShape.PointingHandCursor)
    plus.setToolTip("Pick a picture, app, PDF or slides to find ones like it")
    plus.clicked.connect(on_click)
    return plus


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

    results = Signal(list, int, bool)

    def __init__(self, brain):
        super().__init__()
        self.brain = brain
        self._q = queue.Queue()
        threading.Thread(target=self._run, daemon=True, name="search").start()

    def ask(self, ticket, text, image, kinds, like=None):
        self._q.put((ticket, text, image, kinds, like))

    def _run(self):
        hunter = Hunter(self.brain)
        while True:
            job = self._q.get()
            while not self._q.empty():  # skip queries the user already typed past
                job = self._q.get()
            ticket, text, image, kinds, like = job
            try:
                found, waking = hunter.search(text, image, kinds, like=like)
            except Exception as exc:
                print(f"[lookhound] search failed: {exc}")
                found, waking = [], False
            self.results.emit(found, ticket, waking)


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
        self.texts = [selectable(name)]
        text.addWidget(name)
        folder = Path(result["path"]).parent
        where = QLabel(f"{folder.name or folder}")
        where.setObjectName("muted")
        where.setToolTip(str(folder))
        self.texts.append(selectable(where))
        text.addWidget(where)
        if result["kind"] in ("document", "code", "archive") and preview:
            peek = QLabel(" ".join(preview.split())[:160])
            peek.setObjectName("muted")
            peek.setMinimumWidth(1)  # let the row shrink instead of the preview widening it
            peek.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.texts.append(selectable(peek))
            text.addWidget(peek)
        lay.addLayout(text, 1)

        label = QLabel(KIND_WORDS[result["kind"]].upper())
        label.setObjectName("eyebrow")
        lay.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)

    def selected_text(self):
        return " ".join(t.selectedText() for t in self.texts if t.hasSelectedText())

    def preview_text(self):
        return self.texts[2].text() if len(self.texts) > 2 else ""


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
    update_found = Signal(object)
    update_progress = Signal(float)
    update_ready = Signal(str)
    update_failed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lookhound")
        self.setMinimumSize(600, MIN_HEIGHT)
        self.setAcceptDrops(True)
        self.colors = COLORS
        self.setStyleSheet(stylesheet(self.colors))

        self.prefs = load_prefs()
        width, height = self.prefs.get("window", (860, 760))
        self.resize(width, height)
        self.shelf = Shelf()  # the GUI thread's own read-only look at the index
        self.brain = Brain()
        self.icons = IconMaker()
        self.scout = Scout(self.brain, self.prefs["folders"], self.icons.icon_for)
        self.searcher = Searcher(self.brain)
        self.ticket = 0
        self.query_text = ""
        self.image = None
        self.like = None  # (path, payloads) when searching for files like a document
        self.kinds = None

        self._build()
        self.scout.status.connect(self.status.setText)
        self.scout.count_changed.connect(
            lambda n: self.count.setText(f"Looking after {n:,} files"))
        self.home_timer = QTimer(self, singleShot=True, interval=3000)
        self.home_timer.timeout.connect(
            lambda: self.pages.currentIndex() == 0 and self._refresh_home())
        self.scout.count_changed.connect(lambda _: self.home_timer.start())
        self.searcher.results.connect(self._show_results)
        # While the models are still arriving, quietly search again until they're in.
        self.retry = QTimer(self, singleShot=True, interval=4000)
        self.retry.timeout.connect(lambda: self._search(quiet=True))
        self.update_found.connect(self._show_update)
        self.update_progress.connect(
            lambda f: self.banner_text.setText(f"Downloading Lookhound {self.update['version']}… {f:.0%}"))
        self.update_ready.connect(self._update_downloaded)
        self.update_failed.connect(self._update_failed)
        self.update = None

        self._restore_filter()
        self._refresh_home()
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
        b.setContentsMargins(18, 6, 8, 6)
        b.setSpacing(6)
        self.banner_text = QLabel()
        self.banner_more = QPushButton("Learn more")
        self.banner_more.setObjectName("bannerlink")
        self.banner_button = QPushButton("Update")
        self.banner_button.setObjectName("bannergo")
        later = QPushButton("✕")
        later.setObjectName("bannerlink")
        later.setToolTip("Remind me next time")
        later.clicked.connect(lambda: (self.banner.hide(), self._fit_height()))
        for button in (self.banner_more, self.banner_button, later):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        b.addWidget(self.banner_text, 1)
        b.addWidget(self.banner_more)
        b.addWidget(self.banner_button)
        b.addWidget(later)
        self.banner.hide()
        lay.addWidget(self.banner)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._home_page())
        self.pages.addWidget(self._results_page())
        lay.addWidget(self.pages, 1)

        foot = QHBoxLayout()
        self.status = QLabel("Waking up…")
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
        self._menus()
        self._fit_height()

    def _fit_height(self):
        """Keep the home screen from squashing when a menu bar (Windows) or the
        update bar takes some of the window's height."""
        extra = 0 if self.menuBar().isNativeMenuBar() else self.menuBar().sizeHint().height()
        if not self.banner.isHidden():
            extra += self.banner.sizeHint().height() + self.centralWidget().layout().spacing()
        self.setMinimumHeight(MIN_HEIGHT + extra)
        if self.height() < MIN_HEIGHT + extra:
            self.resize(self.width(), MIN_HEIGHT + extra)

    def _menus(self):
        """The menu bar: on a Mac it sits at the top of the screen. Edit works on
        whatever has focus: the search boxes, or result text you've highlighted."""
        bar = self.menuBar()
        app_menu = bar.addMenu("Lookhound")
        about = QAction("About Lookhound", self, menuRole=QAction.MenuRole.AboutRole)
        about.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(f"{SITE}/about")))
        app_menu.addAction(about)
        news = QAction("What's New", self)
        news.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(changelog_url())))
        app_menu.addAction(news)
        folders = QAction("Folders…", self, menuRole=QAction.MenuRole.PreferencesRole)
        folders.setShortcut(QKeySequence.StandardKey.Preferences)
        folders.triggered.connect(self._edit_folders)
        app_menu.addAction(folders)
        quit_ = QAction("Quit Lookhound", self, menuRole=QAction.MenuRole.QuitRole)
        quit_.setShortcut(QKeySequence.StandardKey.Quit)
        quit_.triggered.connect(self.close)
        app_menu.addAction(quit_)

        edit = bar.addMenu("Edit")
        for entry in [
            ("Undo", QKeySequence.StandardKey.Undo, "undo"),
            ("Redo", QKeySequence.StandardKey.Redo, "redo"),
            None,
            ("Cut", QKeySequence.StandardKey.Cut, "cut"),
            ("Copy", QKeySequence.StandardKey.Copy, "copy"),
            ("Paste", QKeySequence.StandardKey.Paste, "paste"),
            ("Select All", QKeySequence.StandardKey.SelectAll, "selectAll"),
        ]:
            if entry is None:
                edit.addSeparator()
                continue
            label, key, name = entry
            action = QAction(label, self)
            action.setShortcut(key)
            action.triggered.connect(lambda _=False, n=name: self._edit(n))
            edit.addAction(action)

        go = bar.addMenu("Go")
        home = QAction("Home", self)
        home.setShortcut(QKeySequence("Ctrl+Shift+H"))
        home.triggered.connect(self._home)
        go.addAction(home)
        pick = QAction("Find Things Like a File…", self)
        pick.setShortcut(QKeySequence.StandardKey.Open)
        pick.triggered.connect(self._pick_image)
        go.addAction(pick)

    def _edit(self, name):
        widget = QApplication.focusWidget()
        if isinstance(widget, QLineEdit):
            getattr(widget, name)()
        elif isinstance(widget, QLabel):
            if name in ("copy", "cut") and widget.hasSelectedText():
                QApplication.clipboard().setText(widget.selectedText())
            elif name == "selectAll":
                widget.setSelection(0, len(widget.text()))
        elif name == "paste" and self.pages.currentIndex() == 0:
            self.home_query.setFocus()
            self.home_query.paste()

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
            dog_pix = dog_pix.scaledToHeight(int(104 * ratio), Qt.TransformationMode.SmoothTransformation)
            dog_pix.setDevicePixelRatio(ratio)
            dog.setPixmap(dog_pix)
            # The dog is drawn in black ink, so it sits on a little paper card.
            dog.setStyleSheet(f"background: {self.colors['paper']}; border-radius: 22px; padding: 14px;")
            dog.setFixedSize(int(dog_pix.width() / ratio) + 28, int(dog_pix.height() / ratio) + 28)
        dog_row = QHBoxLayout()
        dog_row.addStretch()
        dog_row.addWidget(dog)
        dog_row.addStretch()
        col.addLayout(dog_row)

        greeting, comfort = greet(self.prefs.get("name"))
        self.greeting = QPushButton(greeting.upper())
        self.greeting.setObjectName("greeting")
        self.greeting.setCursor(Qt.CursorShape.PointingHandCursor)
        self.greeting.setToolTip("Tell me what to call you")
        self.greeting.clicked.connect(self._ask_name)
        col.addWidget(self.greeting, 0, Qt.AlignmentFlag.AlignCenter)

        self.hello = hello = QLabel()
        self._new_headline()
        hello.setObjectName("hello")
        hello.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.comfort = sub = QLabel(comfort)
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
        row.addWidget(plus_button(self._pick_image))
        row.addWidget(self.home_query, 1)
        row.addWidget(go)
        col.addLayout(row)

        hint = QLabel("Press + or drop a file here: a picture, an app, a PDF or slides. I'll find ones like it.")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(hint)
        col.addSpacing(18)

        self.recent_box = QWidget()
        self.recent_row = QHBoxLayout(self.recent_box)
        self.recent_row.setContentsMargins(0, 0, 0, 0)
        self.recent_row.setSpacing(4)
        col.addWidget(self.recent_box)

        self.fresh_box = QWidget()
        self.fresh_col = QVBoxLayout(self.fresh_box)
        self.fresh_col.setContentsMargins(0, 6, 0, 0)
        self.fresh_col.setSpacing(0)
        col.addWidget(self.fresh_box)
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

        # Back on the left, the dog in the middle, an equal blank on the right
        # so the dog sits truly centred.
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 4, 0, 0)
        back = QPushButton("←  Back")
        back.setObjectName("quiet")
        back.setStyleSheet("color: palette(text);")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self._home)
        back.setFixedWidth(back.sizeHint().width())
        badge = QLabel()
        badge_pix = QPixmap(str(RESOURCES / "lookhound-128.png"))
        if not badge_pix.isNull():
            ratio = self.devicePixelRatioF()
            badge_pix = badge_pix.scaled(int(44 * ratio), int(44 * ratio),
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
            badge_pix.setDevicePixelRatio(ratio)
            badge.setPixmap(badge_pix)
        badge.setToolTip("Lookhound")
        balance = QWidget()
        balance.setFixedWidth(back.width())
        bar.addWidget(back, 0, Qt.AlignmentFlag.AlignVCenter)
        bar.addStretch()
        bar.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        bar.addStretch()
        bar.addWidget(balance)
        lay.addLayout(bar)

        # What you asked for, as a title rather than another search box.
        asked = QHBoxLayout()
        asked.setSpacing(12)
        asked.addStretch()
        self.asked_thumb = QLabel()
        self.asked_thumb.setFixedSize(40, 40)
        self.asked_thumb.hide()
        self.asked = QLabel()
        self.asked.setObjectName("asked")
        selectable(self.asked)
        asked.addWidget(self.asked_thumb, 0, Qt.AlignmentFlag.AlignVCenter)
        asked.addWidget(self.asked, 0, Qt.AlignmentFlag.AlignVCenter)
        asked.addStretch()
        lay.addLayout(asked)
        lay.addSpacing(4)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip_group = QButtonGroup(self)
        for key, label in CHIPS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            btn.setProperty("key", key)
            self.chip_group.addButton(btn)
            chips.addWidget(btn)
        chips.insertStretch(0)
        chips.addStretch()
        lay.addLayout(chips)

        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Clicking the picture or the space around the text shows the file; the
        # text itself can be highlighted and copied. Right-click for everything else.
        self.list.itemClicked.connect(lambda item: reveal(item.data(Qt.ItemDataRole.UserRole)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._result_menu)
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
        if not text and self.image is None and self.like is None:
            return
        if text:
            self._remember(text)
        self.query_text = text
        if text:
            title = f"“{text}”"
        elif self.like:
            title = f"Files like {self.like[0].name}"
        else:
            title = "Things that look like this"
        metrics = self.asked.fontMetrics()
        self.asked.setText(metrics.elidedText(title, Qt.TextElideMode.ElideRight, 560))
        self.asked.setToolTip(title)
        self.pages.setCurrentIndex(1)
        self._search()

    def _new_headline(self):
        current = self.hello.text()
        self.hello.setText(random.choice([h for h in HEADLINES if h != current]))

    def _home(self):
        self._new_headline()
        self.image = None
        self.like = None
        self.asked_thumb.hide()
        self.home_query.clear()
        self.list.clear()
        self.pages.setCurrentIndex(0)
        self.home_query.setFocus()
        self._refresh_home()

    # ---- the home screen's little helpers ----------------------------------

    def _remember(self, text):
        recent = [t for t in self.prefs.get("recent", []) if t.lower() != text.lower()]
        self.prefs["recent"] = [text] + recent[:RECENT_KEEP - 1]
        save_prefs(self.prefs)

    def _ask_name(self):
        current = self.prefs.get("name") or first_name()
        name, ok = QInputDialog.getText(self, "Lookhound", "What should I call you?", text=current)
        if ok:
            self.prefs["name"] = name.strip()
            save_prefs(self.prefs)
            greeting, comfort = greet(self.prefs["name"])
            self.greeting.setText(greeting.upper())
            self.comfort.setText(comfort)

    def _forget_recent(self):
        self.prefs["recent"] = []
        save_prefs(self.prefs)
        self._refresh_home()

    def _search_for(self, text):
        self.home_query.setText(text)
        self._go()

    def _refresh_home(self):
        clear_layout(self.recent_row)
        recent = self.prefs.get("recent", [])[:3]
        self.recent_box.setVisible(bool(recent))
        if recent:
            self.recent_row.addStretch()
            lead = QLabel("Lately:")
            lead.setObjectName("muted")
            self.recent_row.addWidget(lead)
            for text in recent:
                btn = QPushButton(text if len(text) <= 22 else text[:21].rstrip() + "…")
                btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                btn.setObjectName("recent")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _=False, t=text: self._search_for(t))
                self.recent_row.addWidget(btn)
            forget = QPushButton("forget")
            forget.setObjectName("quiet")
            forget.setToolTip("Clear your recent searches")
            forget.setCursor(Qt.CursorShape.PointingHandCursor)
            forget.clicked.connect(self._forget_recent)
            self.recent_row.addWidget(forget)
            self.recent_row.addStretch()

        clear_layout(self.fresh_col)
        fresh = self.shelf.newest(3)
        self.fresh_box.setVisible(bool(fresh))
        if fresh:
            title = QLabel("NEW ON YOUR COMPUTER")
            title.setObjectName("eyebrow")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.fresh_col.addWidget(title)
            self.fresh_col.addSpacing(6)
            for row in fresh:
                btn = QPushButton(f"{row['name']}    ·    {Path(row['path']).parent.name}")
                btn.setObjectName("fresh")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip(f"Show it in {'Finder' if IS_MAC else 'File Explorer'}")
                btn.clicked.connect(lambda _=False, p=row["path"]: reveal(p))
                self.fresh_col.addWidget(btn)

    def _say(self, text):
        self.list.hide()
        self.empty.setText(text)
        self.empty.show()

    # ---- searching -------------------------------------------------------

    def _set_filter(self, key):
        self.kinds = FILTERS[key]
        self.prefs["filter"] = key
        self._search()

    def _restore_filter(self):
        key = self.prefs.get("filter", "all")
        for btn in self.chip_group.buttons():
            if btn.property("key") == key:
                btn.setChecked(True)
                self.kinds = FILTERS.get(key)

    def _search(self, quiet=False):
        self.retry.stop()
        if self.pages.currentIndex() != 1:
            return
        text = self.query_text
        self.ticket += 1
        if not quiet:
            self._say("Looking…")
        self.searcher.ask(self.ticket, text, self.image, self.kinds, self.like)

    @Slot(list, int, bool)
    def _show_results(self, results, ticket, waking):
        if ticket != self.ticket:
            return
        if waking:
            self.retry.start()
        self.list.clear()
        if not results:
            self._say(WAKING if waking else NOTHING_FOUND)
            return
        self.empty.hide()
        self.list.show()
        place = "Finder" if IS_MAC else "File Explorer"
        for r in results:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r["path"])
            item.setToolTip(f"Click the picture to show it in {place}. Right-click to open or copy.\n{r['path']}")
            card = ResultCard(r, self.colors)
            item.setSizeHint(card.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, card)

    def _result_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        card = self.list.itemWidget(item)
        clipboard = QApplication.clipboard()
        menu = QMenu(self)
        place = "Finder" if IS_MAC else "File Explorer"
        menu.addAction("Open", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        menu.addAction(f"Show in {place}", lambda: reveal(path))
        menu.addSeparator()
        chosen = card.selected_text() if card else ""
        if chosen:
            menu.addAction("Copy", lambda: clipboard.setText(chosen))
        menu.addAction("Copy name", lambda: clipboard.setText(Path(path).name))
        menu.addAction("Copy path", lambda: clipboard.setText(path))
        if card and card.preview_text():
            menu.addAction("Copy preview", lambda: clipboard.setText(card.preview_text()))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # ---- image queries ---------------------------------------------------

    def _image_from_drop(self, mime):
        for url in mime.urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in PICKABLE_EXT:
                return path
        return None

    def dragEnterEvent(self, event):
        if self._image_from_drop(event.mimeData()) or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event):
        mime = event.mimeData()
        path = self._image_from_drop(mime)
        if not path and mime.hasImage():
            path = ICON_CACHE / "dropped.png"
            mime.imageData().save(str(path))
        if path:
            self._use_file(path)

    def _pick_image(self):
        def globs(exts):
            return " ".join("*" + ext for ext in sorted(exts))
        filters = ";;".join([
            f"Anything I know ({globs(PICKABLE_EXT)})",
            f"Pictures ({globs(IMAGE_EXT)})",
            f"Apps ({globs(APP_EXT)})",
            f"Writing ({globs(DOC_EXT)})",
            f"Code ({globs(CODE_EXT)})",
            f"Archives ({globs(ARCHIVE_EXT)})",
        ])
        path, _ = QFileDialog.getOpenFileName(
            self, "Find things like this", str(Path.home()), filters)
        if path:
            self._use_file(Path(path))

    def _use_file(self, path):
        if path.suffix.lower() in LOOKALIKE_EXT:
            self._use_image(path)
            return
        try:
            found = sniff(path)
        except Exception:
            found = None
        if not found:
            return
        # A few chunks, so an archive is compared by what's inside, not just its file list.
        payloads = {space: items[:3] for space, items in found[2].items() if space != "clip"}
        icon = self.icons.provider.icon(QFileInfo(str(path))).pixmap(QSize(80, 80))
        self.image = None
        self.like = (path, payloads)
        self._show_thumb(icon)
        self._go()

    def _show_thumb(self, pix):
        self.asked_thumb.setPixmap(rounded(pix, 40, 12))
        self.asked_thumb.show()

    def _use_image(self, path):
        if path.suffix.lower() in APP_EXT:
            path = self.icons.icon_for(path)
            if not path:
                return
        try:
            image = Image.open(path).convert("RGB")
        except Exception:
            return
        self.image = image
        self.like = None
        self._show_thumb(QPixmap(str(path)))
        self._go()

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
            self.update_found.emit(found)

    @Slot(object)
    def _show_update(self, found):
        """The yellow bar: which version is out, how far behind this one is,
        Learn more (that version's full report on the website) and Update."""
        version, behind = found["version"], found["behind"]
        if behind > 1:
            text = f"You're {behind} versions behind. Lookhound {version} is ready."
        else:
            text = f"Lookhound {version} is ready, with new things and fixes."
        self.update = dict(found, text=text, installer=None)
        self.banner_text.setText(text)
        self.banner_more.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(changelog_url(version))))
        self.banner_button.clicked.connect(self._update_clicked)
        self.banner.show()
        self._fit_height()

    def _update_clicked(self):
        """First click downloads; once it's here, the button restarts into it."""
        if self.update["installer"]:
            updater.restart_into(self.update["installer"])
            self.close()
            QApplication.instance().quit()
            return
        self.banner_button.setEnabled(False)
        self.banner_text.setText(f"Downloading Lookhound {self.update['version']}…")

        def fetch():
            try:
                path = updater.download(self.update["url"], self.update_progress.emit)
            except Exception as exc:
                print(f"[lookhound] update download failed: {exc}")
                self.update_failed.emit()
                return
            self.update_ready.emit(str(path))
        threading.Thread(target=fetch, daemon=True, name="update").start()

    @Slot(str)
    def _update_downloaded(self, path):
        self.update["installer"] = path
        self.banner_text.setText(f"Lookhound {self.update['version']} is ready to install.")
        self.banner_button.setText("Restart to update")
        self.banner_button.setEnabled(True)

    @Slot()
    def _update_failed(self):
        self.banner_text.setText("Couldn't download the update. Check your connection and try again.")
        self.banner_button.setText("Try again")
        self.banner_button.setEnabled(True)

    def changeEvent(self, event):
        # Coming back to the window counts as seeing home again, unless mid-search.
        if (event.type() == event.Type.ActivationChange and self.isActiveWindow()
                and self.pages.currentIndex() == 0 and not self.home_query.text()):
            self._new_headline()
        super().changeEvent(event)

    def closeEvent(self, event):
        self.prefs["window"] = [self.width(), self.height()]
        save_prefs(self.prefs)
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
    # One Lookhound at a time: opening it again just brings this window forward.
    name = "dev.lookhound.window"
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(name)  # left over from a crash
    server = QLocalServer()
    server.listen(name)
    window = Window()
    server.newConnection.connect(lambda: (
        server.nextPendingConnection(), window.showNormal(), window.raise_(),
        window.activateWindow()))
    window.show()
    sys.exit(app.exec())
