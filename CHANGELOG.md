# What's new in Lookhound

The full story of every version. This file is the source for the changelog on
lookhound.spools.studio/changelog and for each GitHub release's notes, so every
release must have a section here.

## 0.3.2
_Released 30 October 2026_

Lookhound now reads whole files, not just their beginnings, and finds exact words as well as meaning. Code, things buried deep inside long documents, and files with underscores in their names are found far more reliably.

### New
- **Exact words.** Alongside searching by meaning, Lookhound now matches the exact words you type, anywhere in a file. Function names, variable names, codes and rare words come up first, which meaning search alone often missed.
- **Code names are split for you.** Searching "handle user login" finds handleUserLogin and handle_user_login.
- **The file you pick comes first.** When you use + (or drop a file) to find files like one you have, that file itself is now shown at the top, followed by similar ones.

### Improvements
- **Whole files are read.** Lookhound used to read only the start of each file: about 1,000 characters of code, a few thousand of a document and the first 12 pages of a PDF. Now it reads the whole thing, up to half a million characters and 400 PDF pages, so a function far down a long file or a sentence near the end of a report is found.
- **Better code results.** The text model now reads 8 pieces spread across each code file instead of just its opening, and the code model reads 2.
- **Names match however you type them.** Searching "client invoices 2024" or "client_invoices_2024.zip" both find client_invoices_2024.zip; dashes, underscores and dots no longer get in the way.
- **One-time refresh.** After updating, Lookhound reads your files again once, in this more thorough way. Search keeps working the whole time, and the status line says when it's done.

### Fixes
- **Searching a file name with an underscore failed.** The underscore was dropped from your search, so names like bali_2025.zip were missed. Fixed.
- **Folders named build, dist, out, target, vendor or env were never searched.** They're now skipped only inside a code project (where they hold generated files), and searched like any other folder elsewhere.
- In our tests, the share of tricky searches that found the right file first went from 6 in 10 to 10 in 10.

## 0.3.1
_Released 28 October 2026_

Updating is now one click: Lookhound downloads the new version, installs it and opens again by itself.

### New
- **Restart to update.** Click **Update** in the yellow bar and Lookhound downloads the new version in the background, showing how far along it is. When it's ready, the button becomes **Restart to update**: Lookhound closes, swaps in the new version where the old one was, and opens again. No dragging into Applications, and no installer windows.
- **Your index and settings come along.** Nothing is read again after an update, except files an older version couldn't open.

### Improvements
- **Fast updates on a Mac.** Updates come as a zip that unpacks in about a minute, instead of the disk image, which could take several.
- **Quiet updates on Windows.** The installer runs in the background with a small progress bar, then Lookhound opens again.
- **Downloads recover.** If your connection drops while an update downloads, it carries on from where it stopped.

### Fixes
- **Safe if something goes wrong.** If an update can't be unpacked, the version you have stays exactly as it was, and the downloaded file is shown so you can install it by hand.

## 0.3.0
_Released 28 October 2026_

Lookhound now tells you when there's a newer version, and every version has a full write-up of what changed, on the Lookhound website.

### New
- **Update alerts.** When a newer Lookhound is out, a yellow bar appears at the top of the window. It says which version is ready and, if you've skipped some, how many versions behind you are.
- **Update button.** Downloads the newest version for your computer and opens the installer.
- **Learn more button.** Opens the full report for the new version on the Lookhound website, with everything new and everything fixed.
- **What's New in the menu bar.** Lookhound menu, then What's New, opens the changelog any time.
- **Changelog on the website.** lookhound.spools.studio/changelog lists every version. Open any of them for its complete report.

### Improvements
- **Files that couldn't be read get another chance after every update.** If a picture or document couldn't be opened by an older version, the next version tries it again automatically, without re-reading anything else.

### Fixes
- **Deleted files are fully forgotten.** Deleting a file or folder removed it from search, but the small preview pictures Lookhound made for it stayed on your disk. They're now deleted too, whether Lookhound was open or not.
- The home screen no longer gets squashed when the update bar is showing, or by the menu bar on Windows.

## 0.2.9
_Released 28 October 2026_

Lookhound reads every file you have, whatever its size, and you can find pictures by the words in their names.

### New
- **No size limits.** Every picture is read however big it is, including huge AI upscales such as Magnific's 10,000 to 16,000 pixel images, which earlier versions skipped completely. Large files of every kind are read from the start instead of being left out.
- **Picture names count.** When a picture's name is made of words, like the prompt AI tools put in file names (magnific_can-you-bring-minar-pakis…), you can search for those words, for example "minar pakistan".
- **Pick or drop archives.** The + button and dropping a file onto the window accept .zip, .rar, .7z and tar archives, and find files like them.
- **Archives are matched by what's inside.** "Files like this" compares the names and text inside an archive, not just its list of files.
- **Clearer relaunch.** Reopening Lookhound says "Welcome back. Checking for anything new…", counts only the files it actually reads, and finishes with "Nothing new since last time" when nothing changed.

### Improvements
- **Big photos open several times faster while indexing.** JPEGs are opened at a small size instead of full resolution. A 16,000 pixel upscale takes about 0.2 seconds.
- **Huge text files are read instantly.** Only the opening of a file is read, so a 200 MB log takes no time.

### Fixes
- A file that truly can't be opened is still found by its name, instead of disappearing from Lookhound.
- Files kept only in iCloud ("Optimise Mac Storage") are found by name without Lookhound downloading them.
- The archive types were missing from the + button's file picker, so Finder greyed them out. They can now be picked.

## 0.2.7
_Released 28 October 2026_

Lookhound has a proper menu bar, so copy and paste work everywhere.

### New
- **Menu bar** with Lookhound, Edit and Go menus at the top of the screen.
- **Edit menu.** Cut, Copy, Paste, Select All, Undo and Redo, with the usual shortcuts. They work in the search box and on result text you've highlighted.
- **Lookhound menu.** About Lookhound, Folders… (Cmd+,) and Quit (Cmd+Q).
- **Go menu.** Home (Cmd+Shift+H) and Find Things Like a File… (Cmd+O).

### Fixes
- Copy and paste didn't work in the search box on a Mac, because Lookhound had no Edit menu.

## 0.2.6
_Released 28 October 2026_

### Changes
- Two home headlines removed: "On the tip of your tongue?" and "I've got a nose for it."

## 0.2.5
_Released 28 October 2026_

### New
- **55 home headlines** instead of 9, all in the same cosy tone. A few of the new ones: "Nose to the ground.", "Who's a good finder?", "Buried in a folder?" and "Leave the looking to me."

## 0.2.4
_Released 28 October 2026_

### New
- **A new headline every time you see it.** The big question on the home screen changes each time you open Lookhound, come back from results, or return to the window. It never shows the same one twice in a row, and never changes while you're typing.

## 0.2.3
_Released 28 October 2026_

More kinds of archive, a tab of their own, and you can copy anything from your results.

### New
- **More archives.** .rar, .7z, .tar, .tar.gz, .tgz, .tar.bz2 and .tar.xz are searchable by the names of the files inside them. Tar archives are also searchable by the text of small files inside.
- **Archives tab** next to Apps, showing only archives.
- **Highlight and copy results.** Drag across a result's name, folder or text preview to highlight it, then copy it.
- **Right-click a result** for Open, Show in Finder, Copy, Copy name, Copy path and Copy preview.

### Improvements
- Encrypted, damaged or unusual archives are still found by their name.

## 0.2.2
_Released 28 October 2026_

### New
- **Zip files are searchable.** Lookhound finds a zip by its name, by the names of the files inside it, and by the opening of up to four small text files it holds. Nothing is unzipped onto your disk, so even a zip holding 15,000 files is read in a fraction of a second.
- Zips show a label, how many files they hold and the first few names.

## 0.2.1
_Released 27 October 2026_

A stability release.

### Fixes
- **Fixed Lookhound quitting unexpectedly on Apple silicon Macs.** Two parts of the app could use the graphics chip at the same moment, which macOS doesn't allow. They now take turns.
- **Files are read only once.** Files waiting for the slower code reading were read again from scratch on every launch. Now each file is read once, and a launch only looks at new or changed files.
- Fixed an error that could happen the very first time Lookhound opened, when several parts of the app set up the index at once.

## 0.2.0
_Released 27 October 2026_

### Changes
- **"New on your computer" shows files by when they arrived,** using the same Date Added that Finder shows, instead of when they were last edited. Editing an old file no longer puts it in the list. On Windows it uses the date the file was created.

## 0.1.9
_Released 27 October 2026_

### Improvements
- **Starts about 4 times faster.** The search models used to take about a minute to load at every launch; now it's about 14 seconds. The app ships its Python libraries already compiled instead of compiling about 10,000 files each time it opens.

### Fixes
- The Mac download builds reliably again with the larger, precompiled app.

## 0.1.6
_Released 27 October 2026_

A big speed release, plus slides and a cleaner results screen.

### New
- **PowerPoint files** (.pptx) are searchable by the text on their slides.
- **Find files like this one.** The + button and dropping a file accept pictures, apps, PDFs, slides, documents and code, and find similar files.
- **Cleaner results screen.** Your search shows as a title under a centred logo, with Back on the left and no second search box.

### Improvements
- **About 10 times faster indexing.** Every file is listed by name in the first second, so names are searchable straight away. Files are then read 32 at a time on your Mac's graphics chip, and code is read more closely in the background afterwards.
- Settings and data files (JSON, XML, YAML, CSS) are read quickly instead of by the slower code reader.
- "New on your computer" only lists files from the last month.

## 0.1.5
_Released 27 October 2026_

### New
- **+ button** next to the search bar to pick a picture from Finder and find ones like it.

### Fixes
- **No more second window.** Lookhound could open a second copy of itself in the background.
- **Searches never hang.** While the search models were still downloading, a search could wait forever. Now it searches with whatever is ready and tries again by itself.
- **Downloads recover.** A dropped connection during the first model download now retries instead of stalling.
- Searching while files are being indexed is faster: indexing pauses for your search.

## 0.1.4
_Released 27 October 2026_

### New
- **A styled install window** for the Mac download, in Lookhound's brown, with an arrow showing where to drag the app.

### Fixes
- **Fully offline after the first run.** Lookhound used to check online for model updates every time it opened, which stalled startup without internet. It now loads the models already on your computer.

## 0.1.3
_Released 26 October 2026_

### Fixes
- The downloaded app now uses its fast search index. Before, it quietly fell back to a slower way of searching.

## 0.1.2
_Released 26 October 2026_

The first public release.

### New
- **Search by describing.** Type what you remember, like "the blue logo" or "that school trip PDF", and Lookhound finds files that match the meaning, even when the name has nothing to do with it.
- **Search by picture.** Drop a picture on the window to find ones like it.
- **Photos, documents, code and apps,** all on your computer. Nothing is uploaded.
- **Filters** for Pictures, Writing, Code and Apps.
- **Live watching.** New and changed files are picked up as they arrive.
- **Click a result** to show it in Finder or File Explorer.
- **Greeting by name,** recent searches, and a list of what's new on your computer.
- Works on Mac (Apple silicon, macOS 14 or newer) and Windows 10 and 11.
