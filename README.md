# Rosetta Magazine Researcher 📚

A fully offline-capable archive viewer with smart search, text formatting, and community catalogs to download magazines along with translations and transcriptions. Designed to seamlessly read scanned PDFs alongside transcribed text, English translations, and rich metadata.

[![Discord Link](https://www.gamingalexandria.com/ga-researcher/discord.png)](https://discord.gg/aFe4YaBT)

## 📥 Download & Installation

Download the latest version for Windows, Mac, or Linux from our **[Releases Page](../../releases)**. 

* **Windows users:** Download the `.exe` file and double-click to run.
* **Mac / Linux users:** Download the `.zip` file, extract it, and run the executable inside.

---

## 🚀 Getting Started
Use the **Library** button to download new issues from the cloud, or the **Search** tab to find specific content inside your downloaded magazines.

**To safely close the application**, simply close your browser tab! The background server will automatically shut down after 20 seconds to save memory.

## 🎮 Viewer Controls
- **Keyboard Navigation:** Use the **Left/Right Arrows** to change pages, and **Page Up/Page Down** to scroll the text boxes!
- **Zoom & Formatting:** Hover your mouse over a page to zoom. Click the **MD** button to toggle between formatted markdown and raw text.
- **Font Size & Theme:** Use the slider at the bottom to adjust text size, and the ☀️ button to switch between Dark and Light mode.
- **Bookmarks:** Click the ⭐ button to save your current page. You can add custom tags to your bookmarks to easily filter them in the Bookmarks sidebar tab!
- **Editing:** Click **✏️ Edit** for quick inline fixes, or use the per-section **Open Editor** buttons for the larger visual editor. Metadata now opens in a structured form. Click **Save** when done!

---

## 🔍 Search & Library Tips
- **Exact Phrases:** Use quotes (`"action packed"`)
- **Exclude Words:** Use a minus sign (`-boring`)
- **Wildcards:** Use an asterisk (`translat*` matches *translator* and *translating*)
- **Filter by Section:** Use the checkboxes in the sidebar to search *only* the Summaries or *only* the English Translations.
- **Adult Content:** In the Library, magazines tagged as 18+/NSFW are hidden by default. Check the "18+ Content" box to include mature content in your Library view.

### Advanced Date Searching
The Search tab has a very smart date filter. You don't need exact days!
- Type `1999` to search the entire year.
- Type `1999/10` or `10-1999` to search a specific month.
- Type `10-31-99` or `1999-10-31` for a specific day.

---

## 📁 Adding Local Magazines (Offline)
You do not have to use the Cloud Library! You can easily add your own personal PDFs to the viewer:
1. Open the **`Magazines`** folder located next to this application.
2. Create a new folder for your magazine (e.g., `My Custom Mag`).
3. Drop your `.pdf` file inside that new folder.
4. *(Optional)* Drop a `.zip` file containing transcriptions and a `metadata.txt` into that same folder! (As long as it's the only ZIP in the folder, or shares the exact same name as the PDF, the app will automatically link them).
5. Restart the app (or just refresh the page), and it will automatically appear in your Search dropdown!

## 📝 Manual Editing & Contributing
If you prefer to edit files manually on your computer (instead of using the Edit button), navigate to the **`Magazines`** folder. The app reads loose `.txt` files or paired `.zip` archives.

**File Naming & Organization:**
For the app to link your PDF with its transcriptions and metadata, they need to be grouped together. The text files must end in `_pXXX.txt` where XXX is the page number. Metadata is completely optional as are all the fields within it.

Here is how your folder should look:
```text
📁 Magazines
  └── 📁 Super Mario Magazine
       ├── Super_Mario_01.pdf           <-- The scanned magazine
       ├── Super_Mario_01.zip           <-- (Optional) ZIP containing text files and metadata.txt
       ├── Super_Mario_01_p001.txt      <-- (If not using a ZIP) Page 1 text
       ├── Super_Mario_01_p002.txt      <-- (If not using a ZIP) Page 2 text
       └── metadata.txt                 <-- (If not using a ZIP) Magazine details
```
*(Note: If you name a file `Super_Mario_01.metadata.txt`, it will apply specifically to that PDF!)*

**Text Files (.txt):**
To ensure the app splits your text into the correct boxes, you must use these exact headers inside your page text files:
```text
#GA-TRANSCRIPTION
(Japanese text goes here)

#GA-TRANSLATION
(English text goes here)

#GA-SUMMARY
(Summary goes here)
```

**Consolidated Master File (_COMPLETE.txt):**
Instead of hundreds of individual `_pXXX.txt` files, you can use a single `_COMPLETE.txt` file with `[[PAGE_XXX]]` markers. This reduces file I/O and speeds up search. The app prioritizes the master file when present, and falls back to individual files for backward compatibility.
```text
[[PAGE_001]]
(Japanese text)
#GA-TRANSLATION
(English text)
#GA-SUMMARY
(Summary)

[[PAGE_002]]
...
```

---

## 📄 Metadata Schema (metadata.txt)
This file sits on your local hard drive next to the PDF (or inside its `.zip`). The app uses this to display info and search. All fields are optional. An example is below:
```text
Magazine Name: Super Mario Magazine
Publisher: Nintendo
Date: 1992-10-01
Issue Name: Volume 1
Region: Japan
Translation: English
Version: 1.1
Tags: action, nes, mario
Scanner: Gaming Alexandria
Scanner URL: https://www.gamingalexandria.com
Editor: Dustin Hubbard
Editor URL: https://www.gamingalexandria.com
Notes: Missing pages 12-14.
```

---

## 📚 The Library Catalog (catalog.json)
While `metadata.txt` handles your local files, the Library tab populates its list of downloadable magazines using a master `catalog.json` file. 

**Official Automatic Updates:**
When you open the Library, the app automatically fetches the latest official catalog from the web. If an official magazine receives a new translation or fix, the app compares the cloud version to your local file and displays an **🔄 Update Available** badge! If you are offline, it safely falls back to reading your local `catalog.json` file.

**Adding Custom Catalogs:**
You can also add third-party magazine lists created by the community! 
1. Create a folder named **`Catalogs`** in the same folder as this application.
2. Place any community `.json` catalog files inside it. 
3. The app will automatically merge them into your Library!

**Creating Your Own Catalog:**
There are two ways to format a custom catalog:

**Format A: Simple Array**
Best for offline sharing or static lists. It is just a raw list of magazines.
* **Pros:** Super simple, no web hosting needed.
* **Cons:** Does not auto-update if the creator adds new magazines.
```json[
  {
    "id": "smm_01",
    "magazine_name": "Super Mario Magazine",
    "date": "1992-10",
    "version": "1.0",
    "pdf_filename": "Super_Mario_Mag_01.pdf",
    "pdf_sources":["https://link-to-pdf.com/file.pdf"]
  }
]
```

**Format B: Auto-Updating Object**
Best for translation groups or creators who continuously release updates.
* **Pros:** Automatically downloads the newest list from the web. Syncs "Update Available" badges!
* **Cons:** Requires you to host the JSON file on a website.
```json
{
  "update_url": "https://yourwebsite.com/catalog.json",
  "items":[
    {
      "id": "smm_01",
      "magazine_name": "Super Mario Magazine",
      "version": "1.1",
      "pdf_filename": "Super_Mario_Mag_01.pdf",
      "pdf_sources":["https://link-to-pdf.com/file.pdf"]
    }
  ]
}
```

**The Transcription ZIP:**
When you download a magazine from the Library, the app fetches two things: the scanned PDF and an optional **transcription ZIP**. The ZIP contains:
- **Page text files** (e.g. `Magazine_01_p001.txt`) — one per page, with `#GA-TRANSCRIPTION`, `#GA-TRANSLATION`, and `#GA-SUMMARY` sections
- **metadata.txt** — magazine metadata (name, publisher, date, tags, credits, etc.)

The ZIP is downloaded from the URLs in `zip_sources` (defined in the catalog by the publisher). If `zip_sources` is empty or missing, the app skips the ZIP and you get only the PDF. The app tries each URL in order until one succeeds, so catalog authors can list backup mirrors.

**Catalog Field Breakdown:**
- `id`: *(Required)* Unique identifier.
- `pdf_filename`: *(Required)* Exact file name the PDF saves as.
- `pdf_sources`: *(Required)* Array of direct download URLs for the PDF.
- `zip_filename` & `zip_sources`: *(Optional)* Filename and array of URLs for the transcription/metadata ZIP. If omitted, defaults to `{pdf_stem}_Data.zip`.
- `version`: Controls the "🔄 Update Available" badge. Compared against local metadata.txt.
- `tags`: Comma-separated list (`"action, retro"`).
- `scanner` & `editor`: *(Optional)* Credits for the community members.
- `scanner_url` & `editor_url`: *(Optional)* Links for the credits.
- `notes`: *(Optional)* Special warnings or notes about the issue.
- `adult_content`: `true` or `false`. Hides issue unless "18+ Content" is checked.
- `original_language` & `translated_language`: Uses 2-letter codes (JP, EN) to generate flags.

---

## ⚙️ Configuration (`config.yaml`)

You can customize the app by placing a `config.yaml` file next to the application (or executable). If the file is missing, sensible defaults are used. All paths are relative to the app root.

```yaml
server:
  port: 18028              # Port for the local web server

paths:
  data_dir: Magazines      # Folder for downloaded magazines
  bookmarks_file: bookmarks.json
  catalog_file: catalog.json
  catalogs_dir: Catalogs
  covers_dir: Covers

catalog:
  urls:                    # Official catalog URLs (tried in order)
    - https://www.gamingalexandria.com/ga-researcher/catalog.json
    - https://archive.org/download/ga-researcher-files/catalog.json

download:
  timeout_seconds: 60      # Timeout for PDF/ZIP downloads
  catalog_fetch_timeout: 10
  cover_fetch_timeout: 5

heartbeat:
  shutdown_after_idle_seconds: 20   # Auto-exit when browser tab is closed
  check_interval_seconds: 5
```

---

## 🛠️ Developer Notes

For local development, you can create a `config.local.yaml` next to `config.yaml`. The app loads `config.yaml` first, then overlays any values from `config.local.yaml`.

For example:

```yaml
server:
  dev_mode: true
```

When `server.dev_mode` is `true`, the app runs with Flask's development reloader. Python and template changes are picked up automatically, and static files are served without cache so a normal browser refresh is enough to see CSS/JS changes. Dev mode also skips the idle auto-shutdown behavior.

---

### Credits
- **App Creator:** Dustin Hubbard (Hubz) - [Gaming Alexandria](https://www.gamingalexandria.com)
- **Powered by:** Python, Flask, PyMuPDF (fitz), and Marked.js

### License (AGPLv3)
Copyright (c) 2026 Gaming Alexandria LLC.

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU Affero General Public License** as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 

*If you modify this software or use it over a network (SaaS), you must release your source code under the same AGPLv3 license.* For the full license text, visit: [https://www.gnu.org/licenses/agpl-3.0.html](https://www.gnu.org/licenses/agpl-3.0.html)
