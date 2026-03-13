#!/usr/bin/env python3
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

import fitz  # PyMuPDF
from flask import Flask, Response, jsonify, render_template_string, request, send_file

app = Flask(__name__)

SERVER_PORT = 18028  # 'Fl' in hex for Flatso

# --- PORTABLE PATH LOGIC ---
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).parent

DATA_DIR = ROOT_DIR / "Magazines"
BOOKMARKS_FILE = ROOT_DIR / "bookmarks.json"
CATALOG_FILE = ROOT_DIR / "catalog.json"
# Add as many backup URLs as you want inside these brackets, wrapped in quotes and separated by commas!
CATALOG_URLS = [
    "https://www.gamingalexandria.com/ga-researcher/catalog.json",
    "https://archive.org/download/ga-researcher-files/catalog.json",
]
CATALOGS_DIR = ROOT_DIR / "Catalogs"
COVERS_DIR = ROOT_DIR / "Covers"

LAST_PING = time.time()
METADATA_CACHE = {}
DOWNLOAD_STATE = {}


# --- SECURITY UTILS ---
def get_safe_path(rel_path: str) -> Path:
    p = (DATA_DIR / rel_path).resolve()
    if not str(p).startswith(str(DATA_DIR.resolve())):
        raise ValueError("Unsafe path traversal detected.")
    return p


# --- UI ---
HTML_UI = r"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Gaming Alexandria Researcher</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root { 
            --accent: #8ab4f8; --bg: #18191a; --panel: #242526; --text: #e4e6eb; 
            --box: #3a3b3c; --border: #3e4042; --font-size: 17px;
        }
        body.light-mode {
            --bg: #f0f2f5; --panel: #ffffff; --text: #1c1e21; --box: #f8f9fa; --border: #dddfe2; --accent: #1a73e8;
        }
        body { display: flex; height: 100vh; margin: 0; font-family: -apple-system, sans-serif; background: var(--bg); color: var(--text); overflow: hidden; }
        #main-container { display: flex; width: 100vw; height: 100vh; overflow: hidden; }
        
        #left { 
            flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; 
            background: #000; position: relative; overflow: hidden; padding-bottom: 80px; 
        }
        #img-container { position: relative; display: inline-block; cursor: crosshair; }
        #page-img { max-width: 100%; max-height: calc(100vh - 120px); object-fit: contain; display: block; }
        
        #magnifier {
            position: absolute; width: 280px; height: 280px; border: 3px solid var(--accent);
            border-radius: 50%; pointer-events: none; display: none;
            box-shadow: 0 0 20px rgba(0,0,0,0.8); background-repeat: no-repeat; z-index: 2000;
        }

        #middle { flex: 1.2; min-width: 0; padding: 30px; overflow-y: auto; background: var(--panel); box-sizing: border-box; border-left: 1px solid var(--border); }
        .sidebar { width: 340px; height: 100%; background: var(--panel); border-left: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }
        .sidebar.collapsed { display: none; }
        
        .controls { 
            position: fixed; bottom: 25px; left: 50%; transform: translateX(-50%); 
            background: #202124; padding: 10px 22px; border-radius: 50px; 
            display: flex; gap: 12px; align-items: center; border: 1px solid #3c4043;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5); z-index: 3000; color: white; white-space: nowrap;
        }
        .ctrl-btn { background: #444; color: white; border: none; padding: 6px 14px; border-radius: 20px; cursor: pointer; font-size: 12px; font-weight: 600; transition: 0.2s; }
        .ctrl-btn:hover, .ctrl-btn.active { background: var(--accent); color: #202124; }
        select, input[type=number], input[type=text] { background: #323639; color: white; border: 1px solid #5f6368; padding: 5px 10px; border-radius: 4px; outline: none; }
        .divider { width: 1px; height: 22px; background: #5f6368; margin: 0 4px; }
        
        .content-box { white-space: pre-wrap; font-family: "MS Mincho", serif; font-size: var(--font-size); line-height: 1.8; background: var(--box); padding: 18px; border-radius: 6px; margin-bottom: 20px; border: 1px solid var(--border); color: var(--text); }
        .content-box.markdown-mode { white-space: normal; word-wrap: break-word; }
        .content-box.markdown-mode table { width: 100%; border-collapse: collapse; margin: 15px 0; border: 1px solid var(--border); }
        .content-box.markdown-mode th, .content-box.markdown-mode td { border: 1px solid var(--border); padding: 10px; }
        .content-box.markdown-mode th { background: rgba(138, 180, 248, 0.1); color: var(--accent); }

        #synopsis-box { border-left: 3px solid var(--accent); background: rgba(138, 180, 248, 0.05); font-style: italic; }
        .section-label { font-size: 10px; font-weight: bold; color: var(--accent); text-transform: uppercase; margin-bottom: 5px; display: block; }
        
        .sidebar-tabs { display: flex; background: #111; }
        .tab { flex: 1; padding: 12px; text-align: center; cursor: pointer; font-size: 11px; font-weight: bold; opacity: 0.5; color: white; }
        .tab.active { opacity: 1; border-bottom: 2px solid var(--accent); background: #222; }
        .sidebar-content { flex-grow: 1; overflow-y: auto; padding: 15px; }
        
        .result-item { padding: 12px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; cursor: pointer; font-size: 13px; position: relative; }
        .result-item:hover { background: var(--box); }
        .del-bk { position: absolute; top: 10px; right: 10px; opacity: 0.4; cursor: pointer; }
        .del-bk:hover { opacity: 1; color: #ff4d4d; }

        /* NETFLIX LIBRARY OVERLAY */
        #library-overlay {
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: var(--bg); z-index: 4000; overflow-y: auto; display: none; flex-direction: column;
        }
        .lib-header {
            padding: 20px 40px; display: flex; flex-direction: column; gap: 15px;
            background: #111; border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 4010;
        }
        .lib-filter-bar {
            display: flex; gap: 15px; align-items: center; background: var(--panel); 
            padding: 12px 20px; border-radius: 8px; border: 1px solid var(--border); flex-wrap: wrap;
        }
        .lib-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 25px; padding: 40px;
        }
        .lib-mag-list { display: flex; flex-wrap: wrap; gap: 15px; padding: 40px; align-content: flex-start; }
        .mag-list-item { background: var(--panel); border: 1px solid var(--border); padding: 12px 24px; border-radius: 30px; cursor: pointer; font-size: 14px; font-weight: bold; color: var(--accent); transition: 0.2s; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .mag-list-item:hover { background: var(--accent); color: #202124; transform: translateY(-2px); }
        .lib-card {
            background: var(--panel); border-radius: 8px; overflow: hidden; cursor: pointer; 
            transition: transform 0.2s, box-shadow 0.2s; position: relative; border: 1px solid var(--border);
        }
        .lib-card:hover { transform: scale(1.05); box-shadow: 0 10px 20px rgba(0,0,0,0.5); border-color: var(--accent); }
        .lib-cover { width: 100%; aspect-ratio: 3/4; object-fit: cover; background: #222; display: block; }
        .lib-info { padding: 12px; }
        .lib-title { font-weight: bold; font-size: 14px; color: var(--accent); margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center;}
        .lib-desc { font-size: 11px; color: #9aa0a6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .badge { position: absolute; top: 10px; right: 10px; padding: 4px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; box-shadow: 0 2px 5px rgba(0,0,0,0.5);}
        .badge-installed { background: #1e8e3e; color: white; }
        .badge-cloud { background: #1a73e8; color: white; }
        .flag-box { font-size: 14px; flex-shrink: 0; margin-left: 8px;}

        /* MODAL POPUP */
        .modal-overlay {
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.85); 
            z-index: 5000; display: none; align-items: center; justify-content: center; backdrop-filter: blur(5px);
        }
        .modal-content {
            background: var(--panel); width: 650px; max-width: 90%; border-radius: 12px; display: flex; 
            overflow: hidden; box-shadow: 0 20px 40px rgba(0,0,0,0.8); border: 1px solid var(--border);
        }
        .modal-left { width: 220px; background: #111; flex-shrink: 0; }
        .modal-left img { width: 100%; height: 100%; object-fit: cover; }
        .modal-right { padding: 30px; flex: 1; position: relative; display: flex; flex-direction: column; }
        .close-modal { position: absolute; top: 15px; right: 20px; cursor: pointer; color: #888; font-size: 24px; font-weight: bold; }
        .close-modal:hover { color: white; }
        
        .btn-dl { background: #1a73e8; color: white; border: none; padding: 10px 16px; border-radius: 4px; font-size: 14px; cursor: pointer; font-weight: bold; width: 100%; margin-top: auto;}
        .btn-dl:hover { background: #1557b0; }
        .btn-read { background: #1e8e3e; color: white; border: none; padding: 10px 16px; border-radius: 4px; font-size: 14px; cursor: pointer; font-weight: bold; width: 100%; margin-top: auto;}
        .btn-read:hover { background: #145c27; }
        .progress-container { width: 100%; background: #222; border-radius: 4px; overflow: hidden; height: 18px; margin-top: auto; position: relative;}
        .progress-bar { height: 100%; background: var(--accent); width: 0%; transition: width 0.3s; }
        .progress-text { position: absolute; width: 100%; text-align: center; font-size: 11px; color: white; top: 2px; font-weight: bold; text-shadow: 1px 1px 2px black;}

        .edit-area { width: 100%; background: #000; color: #fff; font-family: monospace; border: 1px solid #444; padding: 10px; box-sizing: border-box; display: none; margin-bottom: 20px;}
        .check-item { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; cursor: pointer; color: #e8eaed; }
        mark { background: #fde68a; color: #000; border-radius: 2px; font-weight: bold; padding: 0 2px; }
        .meta-line { font-size: 13px; color: #9aa0a6; margin-bottom: 15px; line-height: 1.5; }
        .scanner-link { color: var(--accent); text-decoration: none; font-weight: bold; }
        .scanner-link:hover { text-decoration: underline; }
    </style>
</head>
<body class="dark-mode">
    <div id="main-container">
        <div id="left">
            <div id="img-container">
                <img id="page-img" src="">
                <div id="magnifier"></div>
            </div>
        </div>
        <div id="middle">
            <h2 id="page-title" style="margin-top:0; font-size:22px; color:var(--accent); margin-bottom:5px;">Archive Viewer</h2>
            <div id="meta-display" class="meta-line"></div>
            
            <div id="sec-jp-container">
                <span class="section-label">Transcription</span>
                <div id="jp-box" class="content-box"></div>
                <textarea id="jp-edit" class="edit-area" rows="12"></textarea>
            </div>
            <div id="sec-en-container">
                <span class="section-label">Translation</span>
                <div id="en-box" class="content-box"></div>
                <textarea id="en-edit" class="edit-area" rows="12"></textarea>
            </div>
            <div id="sec-sum-container">
                <span class="section-label">📝 Page Summary</span>
                <div id="synopsis-box" class="content-box"></div>
                <textarea id="sum-edit" class="edit-area" rows="6"></textarea>
            </div>
            
            <div id="meta-edit-container" style="display:none; margin-top:20px;">
                <span class="section-label" style="color:#fde68a;">⚙️ Magazine Metadata (Applies to all pages)</span>
                <textarea id="meta-edit" class="edit-area" rows="6" style="border-color:#fde68a; display:block;"></textarea>
            </div>
        </div>
        <div id="sidebar" class="sidebar collapsed">
            <div class="sidebar-tabs">
                <div id="tab-search" class="tab active" onclick="showTab('search')">SEARCH</div>
                <div id="tab-bookmarks" class="tab" onclick="showTab('bookmarks')">BOOKMARKS</div>
            </div>
            
            <div id="panel-search" class="sidebar-content">
                <input type="text" id="search-in" style="width:100%; margin-bottom:10px;" placeholder='Search term...'>
                
                <input type="text" id="search-mag" list="mag-datalist" style="width:100%; margin-bottom:10px; box-sizing:border-box;" placeholder='Filter by Magazine Name...'>
                <datalist id="mag-datalist"></datalist>
                <input type="text" id="search-tags" style="width:100%; margin-bottom:10px; box-sizing:border-box;" placeholder='Filter by Tags (comma separated)...'>

                <div style="display:flex; gap:10px; margin-bottom:10px;">
                    <input type="text" id="search-date-start" style="width:50%; box-sizing:border-box;" placeholder='Start Date' title="e.g., 1999, 1999/10, 10-31-99" onblur="formatSearchDate(this, 'start')">
                    <input type="text" id="search-date-end" style="width:50%; box-sizing:border-box;" placeholder='End Date' title="e.g., 1999, 1999/10, 10-31-99" onblur="formatSearchDate(this, 'end')">
                </div>

                <div style="display:flex; flex-direction:column; gap:8px; border-bottom:1px solid var(--border); padding-bottom:12px;">
                    <label style="font-size:12px;"><input type="radio" name="scope" value="global" checked> Entire Archive</label>
                    <label style="font-size:12px;"><input type="radio" name="scope" value="current"> Current Issue</label>
                    <label style="font-size:12px;">Include Sections:</label>
                    <div style="display:flex; gap:10px;">
                        <label style="font-size:12px;"><input type="checkbox" id="search-inc-jp" checked> Transcription</label>
                        <label style="font-size:12px;"><input type="checkbox" id="search-inc-en" checked> Translation</label>
                        <label style="font-size:12px;"><input type="checkbox" id="search-inc-sum" checked> Summary</label>
                    </div>
                    
                    <details style="font-size:11px; color:#9aa0a6; margin-top:5px; cursor:pointer;">
                        <summary style="outline:none; font-weight:bold; color:var(--accent);">ℹ️ Advanced Search Tips</summary>
                        <ul style="margin-top:6px; padding-left:20px; margin-bottom:0; line-height:1.6; color:#ccc;">
                            <li><code>"exact phrase"</code> matches exact words.</li>
                            <li><code>-word</code> excludes pages with that word.</li>
                            <li><code>word OR term</code> matches either word.</li>
                            <li><code>translat*</code> acts as a wildcard (matches translator, translating).</li>
                        </ul>
                    </details>
                </div>
                
                <div id="search-results" style="margin-top:15px;"></div>
            </div>
            
            <div id="panel-bookmarks" class="sidebar-content" style="display:none;">
                <input type="text" id="bk-filter" style="width:100%; margin-bottom:15px;" placeholder="Filter tags..." onkeyup="renderBookmarks()">
                <div id="bookmark-list"></div>
            </div>
        </div>
    </div>

    <!-- NETFLIX LIBRARY VIEW -->
    <div id="library-overlay">
        <div class="lib-header">
            <div style="display:flex; justify-content: space-between; align-items: center;">
                <h2 style="margin:0; color:var(--accent);">📚 Community Library</h2>
                <div style="display:flex; gap: 10px;">
                    <button id="lib-update-all-btn" class="ctrl-btn" style="background:#ff9800; color:#000; display:none;" onclick="updateAllIssues()">🔄 Update All</button>
                    <button class="ctrl-btn" style="background:#444;" onclick="toggleLibrary()">✕ Close Library</button>
                </div>
            </div>
            <div class="lib-filter-bar" style="gap:10px; font-size:13px;">
                <input type="text" id="lib-filter-mag" list="lib-mag-datalist" style="flex:1; min-width:140px; box-sizing:border-box;" placeholder="Magazine Name" onkeyup="filterLibrary()">
                <datalist id="lib-mag-datalist"></datalist>

                <input type="text" id="lib-filter-pub" list="lib-pub-datalist" style="width:140px; box-sizing:border-box;" placeholder="Publisher" onkeyup="filterLibrary()">
                <datalist id="lib-pub-datalist"></datalist>

                <input type="text" id="lib-filter-media" list="lib-media-datalist" style="width:110px; box-sizing:border-box;" placeholder="Media Type" onkeyup="filterLibrary()">
                <datalist id="lib-media-datalist"></datalist>

                <input type="text" id="lib-filter-tags" list="lib-tags-datalist" style="width:120px; box-sizing:border-box;" placeholder="Subject Tags" onkeyup="filterLibrary()">
                <datalist id="lib-tags-datalist"></datalist>

                <input type="text" id="lib-filter-orig" list="lib-orig-datalist" style="width:100px; box-sizing:border-box;" placeholder="Original Language" onkeyup="filterLibrary()">
                <datalist id="lib-orig-datalist"></datalist>

                <input type="text" id="lib-filter-trans" list="lib-trans-datalist" style="width:105px; box-sizing:border-box;" placeholder="Translated Language" onkeyup="filterLibrary()">
                <datalist id="lib-trans-datalist"></datalist>

                <div style="display:flex; gap:5px;">
                    <input type="text" id="lib-date-start" style="width:95px; box-sizing:border-box;" placeholder="Start Date" onblur="formatSearchDate(this, 'start'); filterLibrary()" onkeypress="if(event.key==='Enter'){formatSearchDate(this, 'start'); filterLibrary();}">
                    <input type="text" id="lib-date-end" style="width:95px; box-sizing:border-box;" placeholder="End Date" onblur="formatSearchDate(this, 'end'); filterLibrary()" onkeypress="if(event.key==='Enter'){formatSearchDate(this, 'end'); filterLibrary();}">
                </div>
                
                <div style="display:flex; gap:10px; margin-left:auto;">
                    <label class="check-item"><input type="checkbox" id="lib-filter-adult" onchange="filterLibrary()"> 18+ Content</label>
                    <label class="check-item"><input type="checkbox" id="lib-filter-installed" onchange="filterLibrary()"> Hide Installed</label>
                </div>
            </div>
        </div>
        <div id="lib-grid" class="lib-grid"></div>
        <div id="lib-mag-list" class="lib-mag-list" style="display:none;"></div>
    </div>

    <!-- MODAL VIEW -->
    <div id="modal-overlay" class="modal-overlay" onclick="closeModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-left">
                <img id="modal-cover" src="">
            </div>
            <div class="modal-right">
                <span class="close-modal" onclick="closeModal(event)">&times;</span>
                <h2 id="modal-title" style="margin-top:0; margin-bottom:5px; color:var(--accent); font-size:22px;"></h2>
                <div id="modal-meta" style="font-size:13px; color:#9aa0a6; margin-bottom:15px; border-bottom:1px solid var(--border); padding-bottom:15px;"></div>
                <div id="modal-desc" style="font-size:14px; line-height:1.6; margin-bottom:20px;"></div>
                <div id="modal-action-area" style="margin-top:auto;"></div>
            </div>
        </div>
    </div>

    <!-- HELP MODAL -->
    <div id="help-overlay" class="modal-overlay" onclick="closeHelp(event)">
        <div class="modal-content" onclick="event.stopPropagation()" style="width: 800px; max-height: 80vh; flex-direction: column;">
            <div style="padding: 20px 30px; background: #111; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                <h2 style="margin:0; color:var(--accent);">📖 Help & Credits</h2>
                <span style="cursor: pointer; color: #888; font-size: 24px; font-weight: bold;" onclick="closeHelp(event)">&times;</span>
            </div>
            <div id="help-content" class="content-box markdown-mode" style="margin: 0; border: none; border-radius: 0; overflow-y: auto; flex: 1; padding: 30px; background: var(--panel);">
            </div>
        </div>
    </div>

    <div class="controls">
        <button id="lib-btn" class="ctrl-btn" onclick="toggleLibrary()">📚 Library</button>
        <button id="sidebar-toggle" class="ctrl-btn" onclick="toggleSidebar()">🔍 Search</button>
        <div class="divider"></div>
        <button id="help-btn" class="ctrl-btn" onclick="toggleHelp()">❓ Help</button>
        <button id="theme-btn" class="ctrl-btn" onclick="toggleTheme()">☀️</button>
        <button id="md-toggle" class="ctrl-btn active" onclick="toggleMarkdown()">MD</button>
        <button id="edit-btn" class="ctrl-btn" onclick="toggleEdit()">✏️ Edit</button>
        <button id="save-btn" class="ctrl-btn" style="display:none; background:#28a745;" onclick="saveCorrections()">💾 Save</button>
        <button id="bookmark-btn" class="ctrl-btn" onclick="toggleBookmark()">⭐</button>
        <div class="divider"></div>
        <!-- The new searchable input -->
        <input type="text" id="mag-input" list="mag-select-list" style="width:260px; text-overflow: ellipsis;" placeholder="Search installed..." onclick="this.select()">
        <datalist id="mag-select-list"></datalist>
        <!-- The old select, hidden but still holding the backend data -->
        <select id="mag-select" style="display:none;"></select>
        
        <button class="ctrl-btn" onclick="changePage(-1)">Prev</button>
        <input type="number" id="page-num" value="1" min="1" style="width: 48px; text-align: center;">
        <button class="ctrl-btn" onclick="changePage(1)">Next</button>
        <div class="divider"></div>
        <div style="display:flex; align-items:center; gap:12px;">
            <span style="font-size: 11px; color: #9aa0a6;">Font</span>
            <input type="range" min="12" max="40" value="17" oninput="updateFont(this.value)">
            <label class="check-item"><input type="checkbox" id="check-jp" checked onchange="toggleSec('sec-jp-container', this.checked)"> Transcription</label>
            <label class="check-item"><input type="checkbox" id="check-en" checked onchange="toggleSec('sec-en-container', this.checked)"> Translation</label>
            <label class="check-item"><input type="checkbox" id="check-sum" checked onchange="toggleSec('sec-sum-container', this.checked)"> Summary</label>
        </div>
    </div>

    <script>
        // ==========================================
        // EDIT YOUR HELP & CREDITS TEXT HERE:
        // ==========================================
        const HELP_MARKDOWN = `
## Gaming Alexandria Researcher
### Version 0.1 BETA
**Author:** Dustin Hubbard (Hubz) <https://www.gamingalexandria.com>

### Getting Started
Welcome to the Gaming Alexandria Researcher! Use the **Library** to download new issues, or the **Search** tab to find specific content inside your downloaded magazines.

**To safely close the application**, simply close your browser tab! The background server will automatically shut down after 20 seconds to save memory.

### Viewer Controls
- **Keyboard Navigation:** Use the **Left/Right Arrows** to change pages, and **Page Up/Page Down** to scroll the text boxes!
- **Zoom & Formatting:** Hover your mouse over a page to zoom. Click the **MD** button to toggle between formatted markdown and raw text.
- **Font Size & Theme:** Use the slider at the bottom to adjust text size, and the ☀️ button to switch between Dark and Light mode.
- **Bookmarks:** Click the ⭐ button to save your current page. You can add custom tags to your bookmarks to easily filter them in the Bookmarks sidebar tab!
- **Editing:** Click **✏️ Edit** to fix typos in the translation or update the magazine's tags/metadata. Click **Save** when done!

---

### Search & Library Tips
- **Exact Phrases:** Use quotes (\`"action packed"\`)
- **Exclude Words:** Use a minus sign (\`-boring\`)
- **Wildcards:** Use an asterisk (\`translat*\` matches *translator* and *translating*)
- **Filter by Section:** Use the checkboxes in the sidebar to search *only* the Summaries or *only* the English Translations.
- **Adult Content:** In the Library, magazines tagged as 18+/NSFW are hidden by default. Check the "18+ Content" box to include mature content in your Library view.

### Advanced Date Searching
The Search tab has a very smart date filter. You don't need exact days!
- Type \`1999\` to search the entire year.
- Type \`1999/10\` or \`10-1999\` to search a specific month.
- Type \`10-31-99\` or \`1999-10-31\` for a specific day.

---

### Adding Local Magazines (Offline)
You do not have to use the Cloud Library! You can easily add your own personal PDFs to the viewer:
1. Open the **\`Magazines\`** folder located next to this application.
2. Create a new folder for your magazine (e.g., \`My Custom Mag\`).
3. Drop your \`.pdf\` file inside that new folder.
4. *(Optional)* Drop a \`.zip\` file containing transcriptions and a \`metadata.txt\` into that same folder! (As long as it's the only ZIP in the folder, or shares the exact same name as the PDF, the app will automatically link them).
5. Restart the app (or just refresh the page), and it will automatically appear in your Search dropdown!

### Manual Editing & Contributing
If you want to add transcriptions, translations, or metadata to your local magazines, navigate to your **\`Magazines\`** folder. The app reads loose \`.txt\` files or paired \`.zip\` archives.

**File Naming & Organization:**
For the app to link your PDF with its transcriptions and metadata, they need to be grouped together. The text files must end in \`_pXXX.txt\` where XXX is the page number. Metedata is completely optional as are all the fields within it.

Here is how your folder should look:
\`\`\`text
📁 Magazines
  └── 📁 Super Mario Magazine
       ├── Super_Mario_01.pdf           <-- The scanned magazine
       ├── Super_Mario_01.zip           <-- (Optional) ZIP containing text files and metadata.txt
       ├── Super_Mario_01_p001.txt      <-- (If not using a ZIP) Page 1 text
       ├── Super_Mario_01_p002.txt      <-- (If not using a ZIP) Page 2 text
       └── metadata.txt                 <-- (If not using a ZIP) Magazine details
\`\`\`
*(Note: If you name a file \`Super_Mario_01.metadata.txt\`, it will apply specifically to that PDF!)*

**Text Files (.txt):**
To ensure the app splits your text into the correct boxes, you must use these exact headers inside your page text files:
\`\`\`text
#GA-TRANSCRIPTION
(Japanese text goes here)

#GA-TRANSLATION
(English text goes here)

#GA-SUMMARY
(Summary goes here)
\`\`\`

---

### 📄 Metadata Schema (metadata.txt)
This file sits on your local hard drive next to the PDF (or inside its \`.zip\`). The app uses this to display info and search. All the fields are optional. An example is below:
\`\`\`text
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
\`\`\`
*(Tip: Date searching is smart! You can type \`1992\` or \`1992-10\` into the Search bar to find this issue).*

---

### The Library Catalog (catalog.json)
While \`metadata.txt\` handles your local files, the Library tab populates its list of downloadable magazines using a master \`catalog.json\` file. 

**Official Automatic Updates:**
When you open the Library, the app automatically fetches the latest official catalog from the web. If an official magazine receives a new translation or fix, the app compares the cloud version to your local file and displays an **🔄 Update Available** badge! If you are offline, it safely falls back to reading your local \`catalog.json\` file.

**Adding Custom Catalogs:**
You can also add third-party magazine lists created by the community! 
1. Create a folder named **\`Catalogs\`** in the same folder as this application.
2. Place any community \`.json\` catalog files inside it. 
3. The app will automatically merge them into your Library!

**Creating Your Own Catalog:**
If you want to share a custom list of magazines, There are two ways to format a catalog:

**Format A: Simple Array**
Best for offline sharing or static lists. It is just a raw list of magazines.
* **Pros:** Super simple, no web hosting needed.
* **Cons:** Does not auto-update if the creator adds new magazines.
\`\`\`json[
  {
    "id": "smm_01",
    "magazine_name": "Super Mario Magazine",
    "date": "1992-10",
    "version": "1.0",
    "pdf_filename": "Super_Mario_Mag_01.pdf",
    "pdf_sources":["https://link-to-pdf.com/file.pdf"]
  }
]
\`\`\`

**Format B: Auto-Updating Object**
Best for translation groups or creators who continuously release updates.
* **Pros:** Automatically downloads the newest list from the web. Syncs "Update Available" badges!
* **Cons:** Requires you to host the JSON file on a website.
\`\`\`json
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
\`\`\`

**Catalog Field Breakdown:**
- \`id\`: *(Required)* Unique identifier.
- \`pdf_filename\`: *(Required)* Exact file name the PDF saves as.
- \`pdf_sources\`: *(Required)* Array of direct download URLs.
- \`zip_filename\` & \`zip_sources\`: *(Optional)* Array of URLs to download the text/metadata ZIP.
- \`version\`: Controls the "🔄 Update Available" badge. Compared against local metadata.txt.
- \`tags\`: Comma-separated list (\`"action, retro"\`).
- \`scanner\` & \`editor\`: *(Optional)* Credits for the community members.
- \`scanner_url\` & \`editor_url\`: *(Optional)* Links for the credits.
- \`notes\`: *(Optional)* Special warnings or notes about the issue.
- \`adult_content\`: \`true\` or \`false\`. Hides issue unless "18+ Only" is checked.
- \`original_language\` & \`translated_language\`: Uses 2-letter codes (JP, EN) to generate flags.

---
### Credits
- **App Creator:** Dustin Hubbard (Hubz) <https://www.gamingalexandria.com>
- **Powered by:** Python, Flask, PyMuPDF (fitz), and Marked.js

---
### License
Copyright (c) 2026 Gaming Alexandria LLC.

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU Affero General Public License** as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 

*If you modify this software or use it over a network (SaaS), you must release your source code under the same AGPLv3 license.* For the full license text, visit: [https://www.gnu.org/licenses/agpl-3.0.html](https://www.gnu.org/licenses/agpl-3.0.html)
`;
        // ==========================================

        const magSelect = document.getElementById('mag-select');
        
        const pageInput = document.getElementById('page-num');
        const img = document.getElementById('page-img');
        const lens = document.getElementById('magnifier');
        const container = document.getElementById('img-container');

        let isEditing = false;
        let isMarkdown = true; 
        let currentRawData = null;
        let bookmarksData = {};
        let metadataCache = {};
        let localFiles =[];
        let catalogData =[];
        let maxPage = 1;
        const lensZoomFactor = 4;
        let labelToPath = {};
        let pathToLabel = {};
        let itemsWithUpdates =[];
        let completedDownloads = new Set();
        let currentModalItemId = null;

        // HELPER: Convert language codes to emojis
        function getFlagEmoji(langCode) {
            if (!langCode) return "";
            const flags = {
                'JP': '🇯🇵', 'EN': '🇺🇸', 'UK': '🇬🇧', 'FR': '🇫🇷', 
                'DE': '🇩🇪', 'ES': '🇪🇸', 'IT': '🇮🇹', 'KR': '🇰🇷', 'CN': '🇨🇳'
            };
            return flags[langCode.toUpperCase()] || langCode.toUpperCase();
        }

        setInterval(() => { fetch('/api/ping').catch(() => {}); }, 5000);

        container.addEventListener('mousemove', moveLens);
        container.addEventListener('mouseenter', () => lens.style.display = 'block');
        container.addEventListener('mouseleave', () => lens.style.display = 'none');

        function moveLens(e) {
            const rect = img.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();
            let x = e.clientX - rect.left;
            let y = e.clientY - rect.top;
            if (x > rect.width) x = rect.width; if (x < 0) x = 0;
            if (y > rect.height) y = rect.height; if (y < 0) y = 0;
            let lx = e.clientX - containerRect.left;
            let ly = e.clientY - containerRect.top;
            lens.style.left = (lx - lens.offsetWidth / 2) + 'px';
            lens.style.top = (ly - lens.offsetHeight / 2) + 'px';
            lens.style.backgroundSize = (rect.width * lensZoomFactor) + "px " + (rect.height * lensZoomFactor) + "px";
            lens.style.backgroundPosition = "-" + (x * lensZoomFactor - lens.offsetWidth / 2) + "px -" + (y * lensZoomFactor - lens.offsetHeight / 2) + "px";
        }

        async function init(forceUpdate = false) {
            const res = await fetch('/api/list');
            const data = await res.json();
            metadataCache = data.metadata || {};
            localFiles = data.files ||[];
            
            const oldVal = magSelect.value;
            magSelect.innerHTML = '';
            const dl = document.getElementById('mag-select-list');
            dl.innerHTML = '';
            
            labelToPath = {};
            pathToLabel = {};
            
            localFiles.forEach(m => {
                const meta = metadataCache[m] || {};
                let label = meta.name ? meta.name : m.split('/').pop().replace('.pdf','');
                if (meta.date) label += ` (${meta.date})`;
                if (meta.issue_name) label += ` - ${meta.issue_name}`;
                
                labelToPath[label] = m;
                pathToLabel[m] = label;
                
                let opt = document.createElement('option');
                opt.value = m;
                magSelect.appendChild(opt);
                
                let dlOpt = document.createElement('option');
                dlOpt.value = label;
                dl.appendChild(dlOpt);
            });

            let changed = false;
            if (localFiles.includes(oldVal)) {
                magSelect.value = oldVal;
            } else if(localFiles.length > 0) {
                magSelect.value = localFiles[0];
                changed = true;
            }
            
            document.getElementById('mag-input').value = pathToLabel[magSelect.value] || "";

            const magSet = new Set();
            Object.values(metadataCache).forEach(meta => { if (meta.name) magSet.add(meta.name); });
            const dlSearch = document.getElementById('mag-datalist');
            dlSearch.innerHTML = '';
            Array.from(magSet).sort().forEach(m => dlSearch.innerHTML += `<option value="${m}">`);

            // Only force the viewer to reload if the app just booted up, or if the current magazine was deleted!
            if(forceUpdate || changed) {
                if(localFiles.length > 0) update();
            }
            
            fetchBookmarks();
            fetchCatalog();

            if(localFiles.length === 0 && forceUpdate) {
                document.getElementById('library-overlay').style.display = 'flex';
            }
        }
        
        function toggleHelp(forceOpen = false) {
            const overlay = document.getElementById('help-overlay');
            if (overlay.style.display === 'flex' && !forceOpen) {
                closeHelp();
            } else {
                // This line converts your Markdown string into beautiful HTML!
                document.getElementById('help-content').innerHTML = marked.parse(HELP_MARKDOWN);
                overlay.style.display = 'flex';
            }
        }

        function closeHelp(e) {
            if (e && e.target.id !== 'help-overlay' && e.target.innerText !== '×') return;
            document.getElementById('help-overlay').style.display = 'none';
        }

        function toggleLibrary(forceOpen = false) {
            const overlay = document.getElementById('library-overlay');
            if (forceOpen) overlay.style.display = 'flex';
            else overlay.style.display = overlay.style.display === 'flex' ? 'none' : 'flex';
        }

        async function fetchCatalog() {
            const res = await fetch('/api/catalog');
            catalogData = await res.json();
            populateLibraryFilters();
            renderLibrary();
        }

        function populateLibraryFilters() {
            let sets = {
                'lib-mag-datalist': new Set(), 'lib-pub-datalist': new Set(),
                'lib-orig-datalist': new Set(), 'lib-trans-datalist': new Set(),
                'lib-media-datalist': new Set(), 'lib-tags-datalist': new Set()
            };
            
            catalogData.forEach(item => {
                if (item.magazine_name) sets['lib-mag-datalist'].add(item.magazine_name);
                if (item.publisher) sets['lib-pub-datalist'].add(item.publisher);
                if (item.original_language) sets['lib-orig-datalist'].add(item.original_language);
                if (item.translated_language) sets['lib-trans-datalist'].add(item.translated_language);
                if (item.media_type) sets['lib-media-datalist'].add(item.media_type);
                if (item.tags) {
                    let tList = Array.isArray(item.tags) ? item.tags : item.tags.split(',');
                    tList.forEach(t => sets['lib-tags-datalist'].add(t.trim()));
                }
            });
            
            Object.entries(sets).forEach(([id, uniqueSet]) => {
                const dl = document.getElementById(id);
                dl.innerHTML = '';
                Array.from(uniqueSet).sort().forEach(val => { if(val) dl.innerHTML += `<option value="${val}">`; });
            });
        }

        function filterLibrary() { renderLibrary(); }

function renderLibrary() {
            const grid = document.getElementById('lib-grid');
            const magListContainer = document.getElementById('lib-mag-list');
            grid.innerHTML = '';
            magListContainer.innerHTML = '';
            
            const filterMag = document.getElementById('lib-filter-mag').value.toLowerCase();
            const filterPub = document.getElementById('lib-filter-pub').value.toLowerCase();
            const filterOrig = document.getElementById('lib-filter-orig').value.toLowerCase();
            const filterTrans = document.getElementById('lib-filter-trans').value.toLowerCase();
            const filterMedia = document.getElementById('lib-filter-media').value.toLowerCase();
            const filterTags = document.getElementById('lib-filter-tags').value.toLowerCase();
            
            const dateStart = document.getElementById('lib-date-start').value;
            const dateEnd = document.getElementById('lib-date-end').value;
            const adultOnly = document.getElementById('lib-filter-adult').checked;
            const hideInstalled = document.getElementById('lib-filter-installed').checked;
            
            const localFileNames = localFiles.map(f => f.split('/').pop());

            function normalizeCatDate(dStr) {
                if (!dStr) return "";
                let clean = dStr.split('/').join('-').replace(/[^\d\-]/g, '');
                let parts = clean.split('-');
                if (parts.length === 3) {
                    if (parts[0].length === 4) return `${parts[0]}-${parts[1].padStart(2,'0')}-${parts[2].padStart(2,'0')}`;
                    if (parts[2].length === 4) return `${parts[2]}-${parts[0].padStart(2,'0')}-${parts[1].padStart(2,'0')}`;
                } else if (parts.length === 2 && parts[0].length === 4) {
                    return `${parts[0]}-${parts[1].padStart(2,'0')}-01`;
                } else if (parts.length === 1 && parts[0].length === 4) {
                    return `${parts[0]}-01-01`;
                }
                return clean;
            }

            // Mode Toggle: Show list of names by default, show grid if a name is typed/clicked
            if (!filterMag) {
                grid.style.display = 'none';
                magListContainer.style.display = 'flex';
                
                let uniqueMags = new Set();
                catalogData.forEach(item => {
                    if (item.magazine_name) uniqueMags.add(item.magazine_name);
                });
                
                Array.from(uniqueMags).sort().forEach(magName => {
                    const pill = document.createElement('div');
                    pill.className = 'mag-list-item';
                    pill.innerText = magName;
                    pill.onclick = () => {
                        document.getElementById('lib-filter-mag').value = magName;
                        filterLibrary(); // Instantly switch to grid view
                    };
                    magListContainer.appendChild(pill);
                });
                return; // Stop here, do not render covers
            }

            // If a magazine name is typed/selected, show the cover grid!
            grid.style.display = 'grid';
            magListContainer.style.display = 'none';

            itemsWithUpdates =[]; // Reset list

            catalogData.forEach(item => {
                const localRelPath = localFiles.find(f => f.endsWith(item.pdf_filename));
                const isDownloaded = !!localRelPath;
                
                let updateAvailable = false;
                if (isDownloaded) {
                    const localMeta = metadataCache[localRelPath] || {};
                    const localVer = parseFloat(localMeta.version || 0);
                    const catVer = parseFloat(item.version || 0);
                    if (catVer > localVer) {
                        updateAvailable = true;
                        itemsWithUpdates.push(item.id);
                    }
                }
                if (hideInstalled && isDownloaded) return;
                
                const isAdult = item.adult_content === true || String(item.adult_content).toLowerCase() === "true" || 
                                item.adult === true || String(item.adult).toLowerCase() === "true" ||
                                item.nsfw === true || String(item.nsfw).toLowerCase() === "true" ||
                                item.mature === true || String(item.mature).toLowerCase() === "true";
                                
                if (isAdult && !adultOnly) return;

                let prettyName = item.magazine_name || "Unknown Magazine";
                let pub = item.publisher || "";
                let orig = item.original_language || "";
                let trans = item.translated_language || "";
                let media = item.media_type || "";
                let itemTags = item.tags ? (Array.isArray(item.tags) ? item.tags.join(', ') : item.tags) : "";

                if (filterMag && !prettyName.toLowerCase().includes(filterMag)) return;
                if (filterPub && !pub.toLowerCase().includes(filterPub)) return;
                if (filterOrig && !orig.toLowerCase().includes(filterOrig)) return;
                if (filterTrans && !trans.toLowerCase().includes(filterTrans)) return;
                if (filterMedia && !media.toLowerCase().includes(filterMedia)) return;
                if (filterTags && !itemTags.toLowerCase().includes(filterTags)) return;

                if (dateStart || dateEnd) {
                    const normCatDate = normalizeCatDate(item.date);
                    if (!normCatDate) return; 
                    if (dateStart && normCatDate < dateStart) return;
                    if (dateEnd && normCatDate > dateEnd) return;
                }

                let issueLabel = "";
                if(item.date) issueLabel += `${item.date} `;
                if(item.issue_name) issueLabel += `- ${item.issue_name}`;

                let badgeHtml = "";
                if (updateAvailable) badgeHtml = `<div class="badge" style="background:#ff9800; color:#000;">🔄 Update Available</div>`;
                else if (isDownloaded) badgeHtml = `<div class="badge badge-installed">✅ Installed</div>`;
                else badgeHtml = `<div class="badge badge-cloud">☁️ Cloud</div>`;
                
                let origFlag = getFlagEmoji(item.original_language);
                let transFlag = getFlagEmoji(item.translated_language);
                let langDisplay = "";
                if (origFlag && transFlag) langDisplay = `<span class="flag-box">${origFlag} ➔ ${transFlag}</span>`;
                else if (origFlag) langDisplay = `<span class="flag-box">${origFlag}</span>`;

                const coverImg = item.cover_url ? `/api/cover/${encodeURIComponent(item.id)}?v=${encodeURIComponent(item.version || '1.0')}` : 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="%23222"/><text x="50%" y="50%" fill="%23666" font-family="sans-serif" font-size="14" text-anchor="middle">No Cover Art</text></svg>';

                const card = document.createElement('div');
                card.className = 'lib-card';
                card.onclick = () => openModal(item.id, isDownloaded);
                card.innerHTML = `
                    ${badgeHtml}
                    <img class="lib-cover" src="${coverImg}" loading="lazy">
                    <div class="lib-info">
                        <div class="lib-title"><span style="overflow:hidden; text-overflow:ellipsis;">${prettyName}</span> ${langDisplay}</div>
                        <div class="lib-desc">${issueLabel || 'Unknown Issue'}</div>
                    </div>
                `;
                grid.appendChild(card);
            });
            document.getElementById('lib-update-all-btn').style.display = itemsWithUpdates.length > 0 ? 'block' : 'none';
        }

        async function updateAllIssues() {
            if(!confirm(`Update ${itemsWithUpdates.length} issues? Depending on size, this may take a while.`)) return;
            document.getElementById('lib-update-all-btn').innerText = "Starting Updates...";
            document.getElementById('lib-update-all-btn').disabled = true;
            for (let id of itemsWithUpdates) {
                completedDownloads.delete(id); // <--- This clears the memory!
                await fetch(`/api/download`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id}) });
                await new Promise(r => setTimeout(r, 500)); // Stagger slightly
            }
        }

        function openModal(id, isDownloaded) {
            currentModalItemId = id; // Remember exactly which magazine we are looking at!
            const item = catalogData.find(i => i.id === id);
            if(!item) return;

            document.getElementById('modal-cover').src = item.cover_url ? `/api/cover/${encodeURIComponent(item.id)}?v=${encodeURIComponent(item.version || '1.0')}` : 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="%23222"/></svg>';
            
            let prettyName = item.magazine_name || "Unknown Magazine";
            document.getElementById('modal-title').innerText = prettyName;
            
            // Build Modal Metadata
            let metaHtml = "";
            let origFlag = getFlagEmoji(item.original_language);
            let transFlag = getFlagEmoji(item.translated_language);
            
            if(item.issue_name) metaHtml += `<b>Issue:</b> ${item.issue_name}`;
            if(item.date) metaHtml += `<br><b>Date:</b> ${item.date} &nbsp;|&nbsp; `;
            if(item.version) metaHtml += `<b>Version:</b> ${item.version} &nbsp;|&nbsp; `;
            if (origFlag && transFlag) metaHtml += `<b>Language:</b> ${origFlag} ➔ ${transFlag} &nbsp;|&nbsp; `;
            else if (origFlag) metaHtml += `<b>Language:</b> ${origFlag} &nbsp;|&nbsp; `;
            
            let credits =[];
            if (item.scanner) {
                let s = item.scanner_url ? `<a href="${item.scanner_url}" target="_blank" style="color:var(--accent); text-decoration:none;">${item.scanner}</a>` : item.scanner;
                credits.push(`<b>Scanned by:</b> ${s}`);
            }
            if (item.editor) {
                let e = item.editor_url ? `<a href="${item.editor_url}" target="_blank" style="color:var(--accent); text-decoration:none;">${item.editor}</a>` : item.editor;
                credits.push(`<b>Edited by:</b> ${e}`);
            }
            if (credits.length > 0) metaHtml += `<br>` + credits.join(" &nbsp;|&nbsp; ");
            
            document.getElementById('modal-meta').innerHTML = metaHtml;
            
                let descHtml = item.description || "No description provided.";
                
                if (item.notes) {
                    descHtml += `<br><br><span style="color:#fde68a; font-size:13px;"><b>Notes:</b> ${item.notes}</span>`;
                }
            
                // Check JSON for 18+ flag (supports "adult", "nsfw", or "mature")
                if (item.adult_content === true) {
                    descHtml = `<div style="color: #ff4d4d; font-weight: bold; margin-bottom: 12px; border: 1px solid #ff4d4d; padding: 6px 10px; border-radius: 4px; display: inline-block; background: rgba(255, 77, 77, 0.1);">⚠️ Adult Content 18+ ONLY!!</div><br>` + descHtml;
                }
            
                document.getElementById('modal-desc').innerHTML = descHtml;

            const actionArea = document.getElementById('modal-action-area');
            
            fetch('/api/downloads').then(r => r.json()).then(states => {
                const state = states[id];
                if (state && !state.done && !state.error) {
                    actionArea.innerHTML = `
                        <div style="font-size:12px; color:var(--accent); margin-bottom:5px;" id="dl-stat-mod">Downloading...</div>
                        <div class="progress-container">
                            <div class="progress-bar" id="dl-bar-mod" style="width:${state.progress}%"></div>
                            <div class="progress-text" id="dl-txt-mod">${state.progress}%</div>
                        </div>
                    `;
                } else if (itemsWithUpdates.includes(item.id)) {
                    actionArea.innerHTML = `
                        <div style="display:flex; gap:10px;">
                            <button class="btn-read" style="flex:1;" onclick="readIssue('${item.pdf_filename}')">📖 Read Old</button>
                            <button class="btn-dl" style="background:#ff9800; color:#000; flex:1;" onclick="startDownload('${item.id}', this.parentElement)">🔄 Update Now</button>
                            <button class="btn-dl" style="background:#dc3545; flex:none; width:auto; padding:10px 15px;" onclick="uninstallIssue('${item.pdf_filename}')">🗑️ Uninstall</button>
                        </div>
                    `;
                } else if (isDownloaded) {
                    actionArea.innerHTML = `
                        <div style="display:flex; gap:10px;">
                            <button class="btn-read" style="flex:1;" onclick="readIssue('${item.pdf_filename}')">📖 Read Now</button>
                            <button class="btn-dl" style="background:#dc3545; flex:none; width:auto; padding:10px 15px;" onclick="uninstallIssue('${item.pdf_filename}')">🗑️ Uninstall</button>
                        </div>
                    `;
                } else {
                    actionArea.innerHTML = `<button class="btn-dl" onclick="startDownload('${item.id}', this.parentElement)">☁️ Download to Library</button>`;
                }
                document.getElementById('modal-overlay').style.display = 'flex';
            });
        }

        function closeModal(e) {
            if (e && e.target.id !== 'modal-overlay' && !e.target.classList.contains('close-modal')) return;
            document.getElementById('modal-overlay').style.display = 'none';
        }

        async function uninstallIssue(pdf_filename) {
            if(!confirm("Are you sure you want to permanently delete this issue from your computer?")) return;
            document.getElementById('modal-action-area').innerHTML = `<div style="color:#ff4d4d; font-weight:bold; text-align:center;">🗑️ Uninstalling...</div>`;
            await fetch('/api/uninstall', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pdf_filename: pdf_filename})
            });
            closeModal();
            init(); // Refresh grid and dropdowns
        }
        
        async function startDownload(id, actionAreaElement) {
            completedDownloads.delete(id); // <--- This clears the memory!
            actionAreaElement.innerHTML = `
                <div style="font-size:12px; color:var(--accent); margin-bottom:5px;" id="dl-stat-mod">Connecting to Archive...</div>
                <div class="progress-container">
                    <div class="progress-bar" id="dl-bar-mod"></div>
                    <div class="progress-text" id="dl-txt-mod">0%</div>
                </div>
            `;
            await fetch(`/api/download`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id})
            });
            renderLibrary(); 
        }

        setInterval(async () => {
            const overlay = document.getElementById('modal-overlay');
            const libOverlay = document.getElementById('library-overlay');
            if(overlay.style.display !== 'flex' && libOverlay.style.display !== 'flex') return;

            const res = await fetch('/api/downloads');
            const states = await res.json();
            let needsRefresh = false;

            for (const [id, state] of Object.entries(states)) {
                
                // 1. Check if ANY background download just finished
                if (state.done && state.progress === 100) {
                    if (!completedDownloads.has(id)) {
                        completedDownloads.add(id);
                        needsRefresh = true;
                    }
                }

                // 2. Only update the visual progress bar if THIS EXACT item is currently in the popup window!
                if (id === currentModalItemId && overlay.style.display === 'flex') {
                    const statEl = document.getElementById('dl-stat-mod');
                    const barEl = document.getElementById('dl-bar-mod');
                    const txtEl = document.getElementById('dl-txt-mod');
                    
                    if(statEl && barEl) {
                        if (state.error) {
                            statEl.innerText = "Error: " + state.error;
                            statEl.style.color = "#ff4d4d";
                            barEl.style.background = "#ff4d4d";
                        } else {
                            statEl.innerText = state.status;
                            barEl.style.width = state.progress + "%";
                            txtEl.innerText = state.progress + "%";
                            
                            // Only swap to the "Read Now" button if it hasn't been swapped yet
                            if (state.done && state.progress === 100) {
                                const actionArea = document.getElementById('modal-action-area');
                                const item = catalogData.find(i => i.id === id);
                                if(actionArea && item && actionArea.innerHTML.includes('dl-bar-mod')) {
                                    actionArea.innerHTML = `
                                        <div style="display:flex; gap:10px;">
                                            <button class="btn-read" style="flex:1;" onclick="readIssue('${item.pdf_filename}')">📖 Download Complete - Read Now</button>
                                            <button class="btn-dl" style="background:#dc3545; flex:none; width:auto; padding:10px 15px;" onclick="uninstallIssue('${item.pdf_filename}')">🗑️ Uninstall</button>
                                        </div>
                                    `;
                                }
                            }
                        }
                    }
                }
            }
            
            if(needsRefresh) {
                await init(); // Force a refresh of the backend list
                if (libOverlay.style.display === 'flex') filterLibrary(); // Refresh the grid badges!
            }
        }, 1500);

        async function readIssue(filename) {
            // Show loading text while it fetches the new list
            document.getElementById('modal-action-area').innerHTML = `<div style="text-align:center; color:var(--accent); font-weight:bold;">Loading...</div>`;
            
            await init(); // Force a background refresh of the local files list!
            
            const match = localFiles.find(f => f.endsWith(filename));
            if(match) {
                magSelect.value = match;
                pageInput.value = 1;
                update();
            }
            closeModal();
            toggleLibrary(false);
            if (document.body.clientWidth < 800) document.getElementById('sidebar').classList.add('collapsed'); 
        }

        async function update(targetPage = null) {
            if (targetPage) pageInput.value = targetPage;
            const mag = magSelect.value;
            const page = pageInput.value;
            if(!mag) return;
            // Sync the visible search box with the active magazine
            document.getElementById('mag-input').value = pathToLabel[mag] || "";
            
            img.src = `/api/render?mag=${encodeURIComponent(mag)}&page=${page-1}&zoom=1.5&t=${Date.now()}`;
            lens.style.backgroundImage = `url('/api/render?mag=${encodeURIComponent(mag)}&page=${page-1}&zoom=${lensZoomFactor}')`;
            
            const res = await fetch(`/api/text?mag=${encodeURIComponent(mag)}&page=${page}`);
            currentRawData = await res.json();
            maxPage = currentRawData.total_pages;
            
            renderContent();
            renderMetadata(currentRawData.metadata, page, mag);
            
            if(isEditing) { isEditing = false; toggleEdit(); }
        }

        function renderMetadata(meta, page, filename) {
            const titleEl = document.getElementById('page-title');
            const metaEl = document.getElementById('meta-display');
            
            let displayTitle = meta.name || filename.split('/').pop().replace('.pdf','');
            if (meta.issue_name) displayTitle += ` — ${meta.issue_name}`;
            titleEl.innerText = displayTitle + " — Page " + page;

            let metaStr =[];
            if (meta.version) metaStr.push(`<b>Version</b> - <font color="FFFFFF">${meta.version}</font>`);
            if (meta.date) metaStr.push(`<b>Date</b> - <font color="FFFFFF">${meta.date}</font>`);
            if (meta.region) metaStr.push(`<b>Region</b> - <font color="FFFFFF">${meta.region}</font>`);
            if (meta.translation) metaStr.push(`<b>Translation</b> - <font color="FFFFFF">${meta.translation}</font>`);
            if (meta.publisher) metaStr.push(`<b>Publisher</b> - <font color="FFFFFF">${meta.publisher}</font>`);
            
            let credits =[];
            if (meta.scanner) {
                let s = meta.scanner_url ? `<a href="${meta.scanner_url}" target="_blank" class="scanner-link">${meta.scanner}</a>` : meta.scanner;
                credits.push(`<b>Scanned by</b> - ${s}`);
            }
            if (meta.editor) {
                let e = meta.editor_url ? `<a href="${meta.editor_url}" target="_blank" class="scanner-link">${meta.editor}</a>` : meta.editor;
                credits.push(`<b>Edited by</b> - ${e}`);
            }

            let finalHtml = metaStr.join(" • ");
            if (credits.length > 0) finalHtml += (finalHtml ? "<br>" : "") + credits.join(" | ");
            if (meta.tags) finalHtml += `<br><span style="color:#8ab4f8; font-size:12px; font-weight:bold;">Tags - ${meta.tags}</span>`;
            if (meta.notes) finalHtml += `<br><span style="color:#fde68a; font-size:11px;">Notes - ${meta.notes}</span>`;
            
            metaEl.innerHTML = finalHtml;
        }

        function toggleMarkdown() {
            isMarkdown = !isMarkdown;
            document.getElementById('md-toggle').classList.toggle('active', isMarkdown);
            renderContent();
        }

        function renderContent() {
            if (!currentRawData) return;
            const boxes = {'jp-box': currentRawData.jp, 'en-box': currentRawData.en, 'synopsis-box': currentRawData.sum};
            for (const[id, text] of Object.entries(boxes)) {
                const el = document.getElementById(id);
                if (isMarkdown) { el.innerHTML = marked.parse(text); el.classList.add('markdown-mode'); }
                else { el.innerText = text; el.classList.remove('markdown-mode'); }
            }
        }

        function toggleEdit() {
            isEditing = !isEditing;
            const viewBoxes =["jp-box", "en-box", "synopsis-box", "meta-display"];
            const editBoxes =["jp-edit", "en-edit", "sum-edit", "meta-edit-container"];
            
            document.getElementById('edit-btn').classList.toggle('active', isEditing);
            document.getElementById('save-btn').style.display = isEditing ? 'inline-block' : 'none';
            
            if(isEditing) {
                document.getElementById('jp-edit').value = currentRawData.jp;
                document.getElementById('en-edit').value = currentRawData.en;
                document.getElementById('sum-edit').value = currentRawData.sum;
                
                let rawMeta = currentRawData.raw_meta;
                if (!rawMeta) {
                    rawMeta = `Magazine Name: \nPublisher: \nDate: \nIssue Name: \nRegion: \nTranslation: \nVersion: \nTags: \nScanner: \nScanner URL: \nEditor: \nEditor URL: \nNotes: `;
                }
                document.getElementById('meta-edit').value = rawMeta;
            } else { 
                renderContent(); 
            }
            
            viewBoxes.forEach(id => document.getElementById(id).style.display = isEditing ? 'none' : 'block');
            editBoxes.forEach(id => document.getElementById(id).style.display = isEditing ? 'block' : 'none');
        }

        async function saveCorrections() {
            const payload = {
                mag: magSelect.value, page: pageInput.value,
                jp: document.getElementById('jp-edit').value,
                en: document.getElementById('en-edit').value,
                sum: document.getElementById('sum-edit').value,
                meta: document.getElementById('meta-edit').value
            };
            const btn = document.getElementById('save-btn');
            btn.innerText = "Saving...";
            const res = await fetch('/api/save', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if(res.ok) { btn.innerText = "💾 Save"; isEditing = false; toggleEdit(); update(); }
        }

        async function fetchBookmarks() {
            const res = await fetch('/api/bookmarks');
            bookmarksData = await res.json();
            renderBookmarks();
        }

        function renderBookmarks() {
            const list = document.getElementById('bookmark-list');
            const filter = document.getElementById('bk-filter').value.toLowerCase();
            list.innerHTML = "";
            Object.entries(bookmarksData).forEach(([key, b]) => {
                const prettyName = b.mag.split('/').pop().replace('.pdf','');
                if (filter && !b.tags.toLowerCase().includes(filter) && !prettyName.toLowerCase().includes(filter)) return;
                const div = document.createElement('div');
                div.className = 'result-item';
                div.innerHTML = `<b>${prettyName} - P${b.page}</b><br><small style="color:var(--accent)">${b.tags}</small><span class="del-bk" onclick="deleteBookmark('${key}', event)">🗑️</span>`;
                div.onclick = () => { magSelect.value = b.mag; update(b.page); };
                list.appendChild(div);
            });
        }

        async function deleteBookmark(key, e) { e.stopPropagation(); await fetch(`/api/bookmarks?key=${encodeURIComponent(key)}`, { method: 'DELETE' }); fetchBookmarks(); }
        async function toggleBookmark() { const tags = prompt("Enter tags:", ""); if (tags === null) return; await fetch('/api/bookmarks', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ mag: magSelect.value, page: pageInput.value, tags }) }); fetchBookmarks(); }

        function changePage(d) {
            let next = parseInt(pageInput.value) + d;
            if (next >= 1 && next <= maxPage) { pageInput.value = next; update(); }
        }

        // Smart Date Parser
        function formatSearchDate(input, type) {
            let val = input.value.trim().split('/').join('-').split('\\\\').join('-');
            if (!val) return;
            
            if (/^\d{8}$/.test(val)) { val = `${val.substring(0,4)}-${val.substring(4,6)}-${val.substring(6,8)}`; }
            else if (/^\d{6}$/.test(val)) { val = `${val.substring(0,4)}-${val.substring(4,6)}`; }
            else if (/^\d{1,2}-\d{1,2}-\d{4}$/.test(val)) { // MM-DD-YYYY
                let parts = val.split('-');
                val = `${parts[2]}-${parts[0].padStart(2, '0')}-${parts[1].padStart(2, '0')}`;
            }
            else if (/^\d{1,2}-\d{1,2}-\d{2}$/.test(val)) { // MM-DD-YY
                let parts = val.split('-'); let yy = parseInt(parts[2]);
                let yyyy = yy < 50 ? 2000 + yy : 1900 + yy;
                val = `${yyyy}-${parts[0].padStart(2, '0')}-${parts[1].padStart(2, '0')}`;
            }
            else if (/^\d{1,2}-\d{4}$/.test(val)) { // MM-YYYY
                let parts = val.split('-');
                val = `${parts[1]}-${parts[0].padStart(2, '0')}`;
            }
            else if (/^\d{4}-\d{1,2}-\d{1,2}$/.test(val) || /^\d{4}-\d{1,2}$/.test(val)) { // YYYY-M-D
                let parts = val.split('-');
                val = `${parts[0]}` + (parts[1] ? `-${parts[1].padStart(2, '0')}` : '') + (parts[2] ? `-${parts[2].padStart(2, '0')}` : '');
            }

            // Defaults: 1999 -> 1999-01-01 or 1999-12-31
            if (/^\d{4}$/.test(val)) { val = type === 'start' ? `${val}-01-01` : `${val}-12-31`; } 
            else if (/^\d{4}-\d{2}$/.test(val)) {
                if (type === 'start') { val = `${val}-01`; } 
                else {
                    let lastDay = new Date(parseInt(val.split('-')[0]), parseInt(val.split('-')[1]), 0).getDate();
                    val = `${val}-${lastDay}`;
                }
            }
            input.value = val;
        }

        async function executeSearch() {
            const q = document.getElementById('search-in').value;
            const scope = document.querySelector('input[name="scope"]:checked').value;
            const incJp = document.getElementById('search-inc-jp').checked;
            const incEn = document.getElementById('search-inc-en').checked;
            const incSum = document.getElementById('search-inc-sum').checked;
            
            const magFilter = document.getElementById('search-mag').value;
            const dateStart = document.getElementById('search-date-start').value;
            const dateEnd = document.getElementById('search-date-end').value;
            const tagFilter = document.getElementById('search-tags').value;

            const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&scope=${scope}&incJp=${incJp}&incEn=${incEn}&incSum=${incSum}&currentMag=${encodeURIComponent(magSelect.value)}&magFilter=${encodeURIComponent(magFilter)}&dateStart=${encodeURIComponent(dateStart)}&dateEnd=${encodeURIComponent(dateEnd)}&tagFilter=${encodeURIComponent(tagFilter)}`);
            const data = await res.json();
            const container = document.getElementById('search-results');
            container.innerHTML = '';
            
            if (data.results.length === 0) {
                container.innerHTML = '<div style="padding:20px; color:#888;">No matches.</div>';
            } else {
                const countLabel = document.createElement('div');
                countLabel.style.fontSize = "11px";
                countLabel.style.color = "var(--accent)";
                countLabel.style.marginBottom = "10px";
                countLabel.style.fontWeight = "bold";
                countLabel.innerText = `${data.results.length}${data.results.length >= 200 ? '+' : ''} results found`;
                container.appendChild(countLabel);
            }

            data.results.forEach(r => {
                const div = document.createElement('div'); div.className = 'result-item';
                let snip = r.snippet;
                data.terms_to_highlight.forEach(t => {
                    const reHighlight = new RegExp(`(${t.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')})`, 'gi');
                    snip = snip.replace(reHighlight, '<mark>$1</mark>');
                });
                const meta = metadataCache[r.mag] || {};
                let resultTitle = meta.name ? meta.name : r.mag.split('/').pop().replace('.pdf', '');
                if (meta.issue_name) resultTitle += ` - ${meta.issue_name}`;

                div.innerHTML = `<span style="color:var(--accent); font-weight:bold; font-size:11px;">${resultTitle} — P${r.page}</span><br><small>...${snip}...</small>`;
                div.onclick = () => { magSelect.value = r.mag; update(r.page); };
                container.appendChild(div);
            });
        }

        // Attach the Enter key listener to all search input boxes
        ['search-in', 'search-mag', 'search-tags', 'search-date-start', 'search-date-end'].forEach(id => {
            document.getElementById(id).addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    // If they press enter on a date field before clicking away, format it first!
                    if (id.startsWith('search-date')) {
                        formatSearchDate(e.target, id.includes('start') ? 'start' : 'end');
                    }
                    executeSearch();
                }
            });
        });

        function showTab(tab) {
            document.getElementById('tab-search').classList.toggle('active', tab === 'search');
            document.getElementById('tab-bookmarks').classList.toggle('active', tab === 'bookmarks');
            document.getElementById('panel-search').style.display = tab === 'search' ? 'block' : 'none';
            document.getElementById('panel-bookmarks').style.display = tab === 'bookmarks' ? 'block' : 'none';
        }

        function toggleTheme() { document.body.classList.toggle('light-mode'); }
        function toggleSidebar() { document.getElementById('sidebar').classList.toggle('collapsed'); }
        function toggleSec(id, show) { document.getElementById(id).style.display = show ? 'block' : 'none'; }
        function updateFont(v) { document.documentElement.style.setProperty('--font-size', v + 'px'); }
        
        // KEYBOARD SHORTCUTS
        document.addEventListener('keydown', (e) => {
            // 1. Do nothing if the user is typing in a text box or search bar
            const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
            if (activeTag === 'input' || activeTag === 'textarea' || activeTag === 'select') return;
            
            // 2. Do nothing if a menu popup is open (Library, Help, etc.)
            const lib = document.getElementById('library-overlay');
            const mod = document.getElementById('modal-overlay');
            const help = document.getElementById('help-overlay');
            if ((lib && lib.style.display === 'flex') || (mod && mod.style.display === 'flex') || (help && help.style.display === 'flex')) return;

            // 3. Trigger actions based on the key pressed
            if (e.key === 'ArrowLeft') { 
                e.preventDefault(); 
                changePage(-1); 
            } else if (e.key === 'ArrowRight') { 
                e.preventDefault(); 
                changePage(1); 
            } else if (e.key === 'PageUp') { 
                e.preventDefault(); 
                const mid = document.getElementById('middle');
                mid.scrollBy({ top: -(mid.clientHeight * 0.8), behavior: 'smooth' });
            } else if (e.key === 'PageDown') { 
                e.preventDefault(); 
                const mid = document.getElementById('middle');
                mid.scrollBy({ top: (mid.clientHeight * 0.8), behavior: 'smooth' });
            }
        });

        // 1. Instantly load the magazine the moment it is clicked in the dropdown list
        document.getElementById('mag-input').addEventListener('input', (e) => {
            const path = labelToPath[e.target.value];
            if (path) {
                magSelect.value = path;
                pageInput.value = 1;
                update();
                e.target.blur(); // Instantly un-select the box so arrow keys work!
            }
        });

        // 2. If they type nonsense and click away, revert the text back to the actual magazine name
        document.getElementById('mag-input').addEventListener('change', (e) => {
            const path = labelToPath[e.target.value];
            if (!path) {
                e.target.value = pathToLabel[magSelect.value] || "";
            }
        });

        pageInput.onchange = () => { update(); pageInput.blur(); };
        init(true);
    </script>
</body>
</html>
"""

# --- BACKEND ---


def parse_metadata(text: str) -> dict:
    meta = {}
    mapping = {
        "magazine name": "name",
        "publisher": "publisher",
        "date": "date",
        "issue name": "issue_name",
        "scanner": "scanner",
        "scanner url": "scanner_url",
        "editor": "editor",
        "editor url": "editor_url",
        "region": "region",
        "translation": "translation",
        "tags": "tags",
        "version": "version",
        "notes": "notes",
    }
    for line in text.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            clean_key = key.strip().lower()
            if clean_key in mapping:
                meta[mapping[clean_key]] = val.strip()
    return meta


def get_partner_zip(pdf_rel_path: str) -> Path | None:
    pdf_path = DATA_DIR / pdf_rel_path
    if not pdf_path.exists():
        return None
    direct_zip = pdf_path.with_suffix(".zip")
    if direct_zip.exists():
        return direct_zip
    zips_in_folder = list(pdf_path.parent.glob("*.zip"))
    if len(zips_in_folder) == 1:
        return zips_in_folder[0]
    return None


def load_metadata_cache() -> None:
    global METADATA_CACHE
    temp_cache = {}  # Build it in a temporary dictionary first!
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for pdf in DATA_DIR.rglob("*.pdf"):
        rel_path = pdf.relative_to(DATA_DIR).as_posix()
        partner_zip = get_partner_zip(rel_path)
        meta = {}

        if partner_zip:
            try:
                with zipfile.ZipFile(partner_zip, "r") as z:
                    meta_file = next(
                        (
                            n
                            for n in z.namelist()
                            if n.split("/")[-1].lower() == "metadata.txt"
                        ),
                        None,
                    )
                    if meta_file:
                        meta = parse_metadata(
                            z.read(meta_file).decode("utf-8", errors="ignore")
                        )
            except:
                pass

        loose_meta = pdf.with_name(pdf.stem + ".metadata.txt")
        generic_meta = pdf.parent / "metadata.txt"

        if loose_meta.exists():
            meta.update(
                parse_metadata(loose_meta.read_text(encoding="utf-8", errors="ignore"))
            )
        elif generic_meta.exists() and pdf.parent != DATA_DIR:
            meta.update(
                parse_metadata(
                    generic_meta.read_text(encoding="utf-8", errors="ignore")
                )
            )

        temp_cache[rel_path] = meta

    METADATA_CACHE = temp_cache  # Instantly swap it so the UI never sees an empty list


def get_transcription_text(pdf_rel_path: str, page_str: str) -> str | None:
    pdf_path = DATA_DIR / pdf_rel_path
    pattern = re.compile(rf"_p0*{int(page_str)}\.txt$", re.IGNORECASE)

    for lp in pdf_path.parent.glob("*.txt"):
        if pattern.search(lp.name):
            return lp.read_text(encoding="utf-8", errors="ignore")

    partner_zip = get_partner_zip(pdf_rel_path)
    if partner_zip:
        try:
            with zipfile.ZipFile(partner_zip, "r") as z:
                for zname in z.namelist():
                    if pattern.search(zname.split("/")[-1]):
                        return z.read(zname).decode("utf-8", errors="ignore")
        except:
            pass
    return None


def update_zip_content(zip_path: Path, filename: str, new_content: str) -> None:
    temp_fd, temp_path = tempfile.mkstemp(dir=zip_path.parent)
    os.close(temp_fd)
    try:
        replaced = False
        with zipfile.ZipFile(zip_path, "r") as zin:
            with zipfile.ZipFile(
                temp_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as zout:
                for item in zin.infolist():
                    if item.filename.split("/")[-1].lower() == filename.lower():
                        zout.writestr(item.filename, new_content)
                        replaced = True
                    else:
                        zout.writestr(item, zin.read(item.filename))

                # If this page didn't have a file in the ZIP yet, create it
                if not replaced:
                    zout.writestr(filename, new_content)

        time.sleep(0.1)
        os.replace(temp_path, zip_path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e


# --- DOWNLOAD LOGIC ---
def download_waterfall(
    task_id: str, out_path: Path, sources: list, file_type: str
) -> bool:
    if not sources:
        return True
    for url in sources:
        DOWNLOAD_STATE[task_id]["status"] = f"Downloading {file_type}..."
        DOWNLOAD_STATE[task_id]["progress"] = 0
        try:
            # Force no-cache so we never get a stale file from the server
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as response:
                total_size = int(response.headers.get("Content-Length", 0))
                with open(out_path, "wb") as f:
                    downloaded = 0
                    while True:
                        chunk = response.read(16384)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            DOWNLOAD_STATE[task_id]["progress"] = int(
                                (downloaded / total_size) * 100
                            )
            return True
        except Exception as e:
            if out_path.exists():
                out_path.unlink()
            continue
    DOWNLOAD_STATE[task_id]["error"] = f"All {file_type} backups failed."
    return False


def download_worker(task_id: str, item: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_STATE[task_id] = {
        "status": "Initializing...",
        "progress": 0,
        "error": None,
        "done": False,
    }

    temp_dir = DATA_DIR / f".temp_{task_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    pdf_filename = item.get("pdf_filename", "mag.pdf")
    pdf_temp = temp_dir / pdf_filename
    zip_temp = temp_dir / (
        item.get("zip_filename") or f"{Path(pdf_filename).stem}_Data.zip"
    )

    success_pdf = download_waterfall(
        task_id, pdf_temp, item.get("pdf_sources", []), "PDF"
    )
    if not success_pdf:
        DOWNLOAD_STATE[task_id]["done"] = True
        return

    success_zip = download_waterfall(
        task_id, zip_temp, item.get("zip_sources", []), "Data ZIP"
    )
    if not success_zip:
        DOWNLOAD_STATE[task_id]["done"] = True
        return

    DOWNLOAD_STATE[task_id]["status"] = "Organizing..."
    meta = {}
    if success_zip and zip_temp.exists():
        try:
            with zipfile.ZipFile(zip_temp, "r") as z:
                meta_file = next(
                    (
                        n
                        for n in z.namelist()
                        if n.split("/")[-1].lower() == "metadata.txt"
                    ),
                    None,
                )
                if meta_file:
                    meta = parse_metadata(
                        z.read(meta_file).decode("utf-8", errors="ignore")
                    )
        except:
            pass

    mag_name = (
        meta.get("name", item.get("magazine_name", "Unsorted"))
        .replace("/", "_")
        .replace("\\", "_")
    )
    date_str = (
        meta.get("date", item.get("date", "")).replace("/", "-").replace("\\", "-")
    )
    issue_name = (
        meta.get("issue_name", item.get("issue_name", ""))
        .replace("/", "_")
        .replace("\\", "_")
    )

    folder_name = ""
    if date_str and issue_name:
        folder_name = f"{date_str} - {issue_name}"
    elif issue_name:
        folder_name = issue_name
    elif date_str:
        folder_name = date_str

    final_dir = DATA_DIR / mag_name
    if folder_name:
        final_dir = final_dir / folder_name
    final_dir.mkdir(parents=True, exist_ok=True)

    if success_pdf and pdf_temp.exists():
        os.replace(pdf_temp, final_dir / item.get("pdf_filename"))
    if success_zip and zip_temp.exists():
        os.replace(zip_temp, final_dir / zip_temp.name)

    # Clean up any old loose text files so they don't override the fresh ZIP transcriptions
    for old_txt in final_dir.glob(
        f"{Path(item.get('pdf_filename', 'mag.pdf')).stem}_p*.txt"
    ):
        try:
            old_txt.unlink()
        except:
            pass

    # 1. Build the fresh metadata content from the Catalog
    ml = []
    if item.get("magazine_name"):
        ml.append(f"Magazine Name: {item['magazine_name']}")
    if item.get("publisher"):
        ml.append(f"Publisher: {item['publisher']}")
    if item.get("date"):
        ml.append(f"Date: {item['date']}")
    if item.get("issue_name"):
        ml.append(f"Issue Name: {item['issue_name']}")
    if item.get("original_language"):
        ml.append(f"Region: {item['original_language']}")
    if item.get("translated_language"):
        ml.append(f"Translation: {item['translated_language']}")
    if item.get("version"):
        ml.append(f"Version: {item['version']}")
    if item.get("tags"):
        ml.append(f"Tags: {item['tags']}")
    if item.get("scanner"):
        ml.append(f"Scanner: {item['scanner']}")
    if item.get("scanner_url"):
        ml.append(f"Scanner URL: {item['scanner_url']}")
    if item.get("editor"):
        ml.append(f"Editor: {item['editor']}")
    if item.get("editor_url"):
        ml.append(f"Editor URL: {item['editor_url']}")
    if item.get("notes"):
        ml.append(f"Notes: {item['notes']}")
    meta_content = "\n".join(ml)

    # 2. Force Overwrite: Inject into ZIP if ZIP exists, otherwise save as loose file
    pdf_filename = item.get("pdf_filename", "mag.pdf")
    zip_filename = item.get("zip_filename") or f"{Path(pdf_filename).stem}_Data.zip"
    zip_path = final_dir / zip_filename
    loose_meta = final_dir / f"{Path(pdf_filename).stem}.metadata.txt"

    if zip_path.exists():
        try:
            update_zip_content(zip_path, "metadata.txt", meta_content)
            if loose_meta.exists():
                os.remove(loose_meta)  # Clean up old loose files
        except Exception:
            pass
    else:
        loose_meta.write_text(meta_content, encoding="utf-8")

    try:
        shutil.rmtree(temp_dir)
    except:
        pass

    DOWNLOAD_STATE[task_id]["progress"] = 100
    DOWNLOAD_STATE[task_id]["status"] = "Complete!"
    DOWNLOAD_STATE[task_id]["done"] = True
    load_metadata_cache()


# --- STANDARD ENDPOINTS ---
def get_all_catalogs() -> list:
    catalogs = []

    # 1. Main Official Catalog (With Fallback Backups)
    official_loaded = False
    if CATALOG_URLS:
        urls_to_try = CATALOG_URLS if isinstance(CATALOG_URLS, list) else [CATALOG_URLS]
        for url in urls_to_try:
            if not url:
                continue
            try:
                # Use a real-browser disguise to bypass server firewalls and increase timeout to 10s!
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        "Accept": "application/json, text/plain, */*",
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    raw_data = r.read().decode("utf-8")
                    main_data = json.loads(raw_data)
                    catalogs.extend(
                        main_data.get("items", main_data)
                        if isinstance(main_data, dict)
                        else main_data
                    )
                    official_loaded = True
                    # Cache it to the hard drive for offline fallback!
                    try:
                        CATALOG_FILE.write_text(raw_data, encoding="utf-8")
                    except:
                        pass
                    break  # Success! Stop trying backup URLs.
            except Exception as e:
                print(f"Failed to load catalog from {url}: {e}")
                continue  # Failed. Move to the next backup URL in the list.

    # Fallback to local offline file if ALL cloud URLs fail (or if list is empty)
    if not official_loaded and CATALOG_FILE.exists():
        try:
            main_data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
            catalogs.extend(
                main_data.get("items", main_data)
                if isinstance(main_data, dict)
                else main_data
            )
        except Exception as e:
            print(f"Failed to load local catalog: {e}")

    # 2. Custom Community Catalogs
    CATALOGS_DIR.mkdir(parents=True, exist_ok=True)
    for c_file in CATALOGS_DIR.glob("*.json"):
        try:
            c_data = json.loads(c_file.read_text(encoding="utf-8"))
            # Auto-update custom catalog if it has a URL
            if isinstance(c_data, dict) and "update_url" in c_data:
                try:
                    req = urllib.request.Request(
                        c_data["update_url"],
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                        },
                    )
                    with urllib.request.urlopen(req, timeout=10) as r:
                        new_data = json.loads(r.read().decode("utf-8"))
                        c_file.write_text(
                            json.dumps(new_data, indent=4), encoding="utf-8"
                        )
                        c_data = new_data
                except:
                    pass

            items = c_data.get("items", c_data) if isinstance(c_data, dict) else c_data
            catalogs.extend(items)
        except Exception as e:
            print(f"Failed to load custom catalog {c_file}: {e}")

    return catalogs


@app.route("/api/cover/<item_id>")
def get_cover(item_id: str) -> Response:
    v = request.args.get("v", "1.0")
    safe_id = "".join(c for c in item_id if c.isalnum() or c in "_-")
    cache_name = f"{safe_id}_v{v}.cache"

    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = COVERS_DIR / cache_name

    # 1. If we already have it, serve it instantly from the hard drive
    if cache_path.exists():
        return send_file(cache_path, mimetype="image/jpeg")

    # 2. If we don't have it, find the URL in the catalog
    catalogs = get_all_catalogs()
    item = next((i for i in catalogs if str(i.get("id")) == item_id), None)

    if item and item.get("cover_url"):
        try:
            req = urllib.request.Request(
                item["cover_url"], headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                img_data = response.read()

                # Cleanup older versions of this cover to save space
                for old_file in COVERS_DIR.glob(f"{safe_id}_v*.cache"):
                    try:
                        old_file.unlink()
                    except:
                        pass

                # Save the new cover and serve it
                cache_path.write_bytes(img_data)
                return send_file(io.BytesIO(img_data), mimetype="image/jpeg")
        except Exception:
            pass

    # 3. Fallback: Return a clean missing cover image if download fails
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="#222"/><text x="50%" y="50%" fill="#666" font-family="sans-serif" font-size="14" text-anchor="middle">No Cover Art</text></svg>'
    return send_file(io.BytesIO(svg.encode("utf-8")), mimetype="image/svg+xml")


@app.route("/api/catalog")
def get_catalog() -> Response:
    return jsonify(get_all_catalogs())


@app.route("/api/download", methods=["POST"])
def start_download() -> Response | tuple[Response, int]:
    data = request.json
    item_id = data.get("id")
    catalog = get_all_catalogs()
    item = next((i for i in catalog if i["id"] == item_id), None)
    if item:
        threading.Thread(
            target=download_worker, args=(item_id, item), daemon=True
        ).start()
        return jsonify({"status": "started"})
    return jsonify({"error": "Item not found"}), 404


@app.route("/api/downloads")
def get_downloads() -> Response:
    return jsonify(DOWNLOAD_STATE)


@app.route("/api/list")
def list_mags() -> Response:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    mags = [p.relative_to(DATA_DIR).as_posix() for p in DATA_DIR.rglob("*.pdf")]
    load_metadata_cache()
    return jsonify({"files": sorted(mags), "metadata": METADATA_CACHE})


@app.route("/api/render")
def render_page() -> Response | tuple[Response, int]:
    mag = request.args.get("mag", "")
    pn = int(request.args.get("page", 0))
    zoom = float(request.args.get("zoom", 1.5))
    try:
        pdf_path = get_safe_path(mag)
        doc = fitz.open(pdf_path)
        page = doc.load_page(pn)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img = pix.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(img), mimetype="image/png")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/text")
def get_text() -> Response:
    mag_rel_path = request.args.get("mag", "")
    pg = request.args.get("page", "1").zfill(3)
    content = get_transcription_text(mag_rel_path, pg)

    total = 0
    try:
        doc = fitz.open(get_safe_path(mag_rel_path))
        total = len(doc)
        doc.close()
    except:
        pass

    jp, en, sum_t = "No transcription found for this page.", "", ""
    if content:
        content = re.sub(r"^#\s?GA-TRANSCRIPTION\s*", "", content, flags=re.IGNORECASE)
        parts = re.split(r"#\s?GA-TRANSLATION", content, flags=re.IGNORECASE)

        if len(parts) > 1:
            jp = parts[0].strip()
            sub = re.split(r"#\s?GA-SUMMARY", parts[1], flags=re.IGNORECASE)
            en = sub[0].strip()
            sum_t = sub[1].strip() if len(sub) > 1 else ""
        else:
            # If there is no Translation tag, check the first part for the Summary tag
            sub = re.split(r"#\s?GA-SUMMARY", parts[0], flags=re.IGNORECASE)
            jp = sub[0].strip()
            sum_t = sub[1].strip() if len(sub) > 1 else ""

    meta = METADATA_CACHE.get(mag_rel_path, {})

    raw_meta = ""
    partner_zip = get_partner_zip(mag_rel_path)
    pdf_path = DATA_DIR / mag_rel_path

    if partner_zip:
        try:
            with zipfile.ZipFile(partner_zip, "r") as z:
                meta_file = next(
                    (
                        n
                        for n in z.namelist()
                        if n.split("/")[-1].lower() == "metadata.txt"
                    ),
                    None,
                )
                if meta_file:
                    raw_meta = z.read(meta_file).decode("utf-8", errors="ignore")
        except:
            pass
    else:
        loose_meta = pdf_path.with_name(pdf_path.stem + ".metadata.txt")
        generic_meta = pdf_path.parent / "metadata.txt"
        if loose_meta.exists():
            raw_meta = loose_meta.read_text(encoding="utf-8", errors="ignore")
        elif generic_meta.exists() and pdf_path.parent != DATA_DIR:
            raw_meta = generic_meta.read_text(encoding="utf-8", errors="ignore")

    return jsonify(
        {
            "jp": jp,
            "en": en,
            "sum": sum_t,
            "total_pages": total,
            "metadata": meta,
            "raw_meta": raw_meta,
        }
    )


@app.route("/api/save", methods=["POST"])
def save_text() -> Response | tuple[Response, int]:
    data = request.json
    rel_path = data["mag"]
    page_num = int(data["page"])
    pdf_path = get_safe_path(rel_path)
    content = f"#GA-TRANSCRIPTION\n{data['jp']}\n\n#GA-TRANSLATION\n{data['en']}\n\n#GA-SUMMARY\n{data['sum']}"
    pattern = re.compile(rf"_p0*{page_num}\.txt$", re.IGNORECASE)
    meta_content = data.get("meta", "")

    try:
        partner_zip = get_partner_zip(rel_path)
        if partner_zip:
            # 1. Update the Page Text File
            target_filename = f"{pdf_path.stem}_p{str(page_num).zfill(3)}.txt"
            with zipfile.ZipFile(partner_zip, "r") as z:
                for zname in z.namelist():
                    if pattern.search(zname.split("/")[-1]):
                        target_filename = zname
                        break
            update_zip_content(partner_zip, target_filename, content)

            # 2. Update the Metadata File
            update_zip_content(partner_zip, "metadata.txt", meta_content)
        else:
            # 1. Update Loose Page Text File
            target_filepath = None
            for lp in pdf_path.parent.glob("*.txt"):
                if pattern.search(lp.name):
                    target_filepath = lp
                    break
            if not target_filepath:
                target_filepath = (
                    pdf_path.parent / f"{pdf_path.stem}_p{str(page_num).zfill(3)}.txt"
                )
            target_filepath.write_text(content, encoding="utf-8")

            # 2. Update Loose Metadata File
            loose_meta = pdf_path.with_name(pdf_path.stem + ".metadata.txt")
            generic_meta = pdf_path.parent / "metadata.txt"

            if generic_meta.exists() and pdf_path.parent != DATA_DIR:
                generic_meta.write_text(meta_content, encoding="utf-8")
            else:
                loose_meta.write_text(meta_content, encoding="utf-8")

        load_metadata_cache()  # Reload the server cache instantly
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search")
def search() -> Response:
    query = request.args.get("q", "")
    scope = request.args.get("scope", "global")
    inc_jp = request.args.get("incJp") == "true"
    inc_en = request.args.get("incEn") == "true"
    inc_sum = request.args.get("incSum") == "true"
    current_mag = request.args.get("currentMag", "")
    mag_filter = request.args.get("magFilter", "").lower()
    date_start = request.args.get("dateStart", "")
    date_end = request.args.get("dateEnd", "")
    tag_filter = request.args.get("tagFilter", "").lower()

    def normalize_meta_date(d_str: str) -> str:
        if not d_str:
            return ""
        clean = re.sub(r"[^\d\-\/]", "", d_str).replace("/", "-")
        parts = clean.split("-")
        if len(parts) == 3:
            if len(parts[0]) == 4:
                return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
            elif len(parts[2]) == 4:
                return f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
        elif len(parts) == 2 and len(parts[0]) == 4:
            return f"{parts[0]}-{parts[1].zfill(2)}-01"
        elif len(parts) == 1 and len(parts[0]) == 4:
            return f"{parts[0]}-01-01"
        return clean

    # Smart Query Parsing
    neg_exact = re.findall(r'-"([^"]+)"', query)
    query = re.sub(r'-"[^"]+"', "", query)

    exact_phrases = re.findall(r'"([^"]+)"', query)
    query = re.sub(r'"[^"]+"', "", query)

    raw_terms = query.split()
    neg_terms = [t[1:] for t in raw_terms if t.startswith("-") and len(t) > 1]
    pos_terms_raw = [t for t in raw_terms if not t.startswith("-")]

    # Handle OR logic and wildcards (*)
    pos_query = " ".join(pos_terms_raw)
    or_groups_raw = pos_query.split(" OR ")

    def term_to_regex(term: str) -> str:
        return re.escape(term.lower()).replace(r"\*", ".*")

    or_groups = []
    for grp in or_groups_raw:
        terms = grp.split()
        if terms:
            or_groups.append([term_to_regex(t) for t in terms])

    highlight_list = exact_phrases + [
        t.replace("*", "") for t in pos_terms_raw if t != "OR"
    ]
    results = []

    def scan_text(text: str, mag_rel_path: str, page_num: int) -> None:
        # 1. Isolate text sections
        clean_text = re.sub(r"^#\s?GA-TRANSCRIPTION\s*", "", text, flags=re.IGNORECASE)
        parts = re.split(r"#\s?GA-TRANSLATION", clean_text, flags=re.IGNORECASE)
        en_text, sum_text = "", ""

        if len(parts) > 1:
            jp_text = parts[0]
            sub = re.split(r"#\s?GA-SUMMARY", parts[1], flags=re.IGNORECASE)
            en_text = sub[0]
            sum_text = sub[1] if len(sub) > 1 else ""
        else:
            sub = re.split(r"#\s?GA-SUMMARY", parts[0], flags=re.IGNORECASE)
            jp_text = sub[0]
            sum_text = sub[1] if len(sub) > 1 else ""
            if len(sub) > 1:
                jp_text, sum_text = sub[0], sub[1]

        # 2. Build searchable text based on toggles
        searchable_text = ""
        if inc_jp:
            searchable_text += jp_text + " "
        if inc_en:
            searchable_text += en_text + " "
        if inc_sum:
            searchable_text += sum_text + " "

        if not searchable_text.strip():
            return
        blob = searchable_text.lower()

        # 3. Process Negative Filters
        if any(nep.lower() in blob for nep in neg_exact):
            return
        if any(nt.lower() in blob for nt in neg_terms):
            return

        # 4. Process Exact Phrases
        if any(ep.lower() not in blob for ep in exact_phrases):
            return

        # 5. Process Positive Terms (AND / OR Groups)
        if or_groups:
            group_matched = False
            for grp in or_groups:
                if all(re.search(t_regex, blob) for t_regex in grp):
                    group_matched = True
                    break
            if not group_matched:
                return

        # 6. Snippet Generation
        first_match = 0
        if exact_phrases:
            first_match = blob.find(exact_phrases[0].lower())
        elif pos_terms_raw and pos_terms_raw[0] != "OR":
            m = re.search(or_groups[0][0], blob) if or_groups and or_groups[0] else None
            if m:
                first_match = m.start()

        idx = max(0, first_match)
        clean_val = searchable_text.replace("\\n", " ").replace("\n", " ")
        snippet = clean_val[max(0, idx - 40) : min(len(clean_val), idx + 60)]
        results.append({"mag": mag_rel_path, "page": page_num, "snippet": snippet})

    # Standard loop to search through files and ZIPs
    for mag_rel_path in METADATA_CACHE.keys():
        if scope == "current" and mag_rel_path != current_mag:
            continue

        meta = METADATA_CACHE.get(mag_rel_path, {})

        # 1. Magazine Name Filter
        if mag_filter and mag_filter not in meta.get("name", "").lower():
            continue

        # 1.5 Tags Filter
        if tag_filter:
            meta_tags = meta.get("tags", "").lower()
            if not all(
                t.strip() in meta_tags for t in tag_filter.split(",") if t.strip()
            ):
                continue

        # 2. Date Range Filter
        if date_start or date_end:
            m_date = meta.get("date", "")
            if not m_date:
                continue  # Skip if no date is found

            norm_m_date = normalize_meta_date(m_date)
            if not norm_m_date:
                continue

            if date_start and norm_m_date < date_start:
                continue
            if date_end and norm_m_date > date_end:
                continue

        pdf_path = DATA_DIR / mag_rel_path

        for txt in pdf_path.parent.glob("*.txt"):
            m = re.search(r"_p(\d+)\.txt$", txt.name, re.IGNORECASE)
            if m:
                scan_text(
                    txt.read_text(encoding="utf-8", errors="ignore"),
                    mag_rel_path,
                    int(m.group(1)),
                )

        partner_zip = get_partner_zip(mag_rel_path)
        if partner_zip:
            try:
                with zipfile.ZipFile(partner_zip, "r") as z:
                    for zname in z.namelist():
                        if zname.lower().endswith(".txt"):
                            m = re.search(
                                r"_p(\d+)\.txt$", zname.split("/")[-1], re.IGNORECASE
                            )
                            if m:
                                scan_text(
                                    z.read(zname).decode("utf-8", errors="ignore"),
                                    mag_rel_path,
                                    int(m.group(1)),
                                )
            except:
                pass

    return jsonify({"results": results[:200], "terms_to_highlight": highlight_list})


@app.route("/api/bookmarks", methods=["GET", "POST", "DELETE"])
def bookmarks_handler() -> Response:
    if not BOOKMARKS_FILE.exists():
        BOOKMARKS_FILE.write_text("{}", encoding="utf-8")
    bks = json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
    if request.method == "POST":
        d = request.json
        bks[f"{d['mag']}_{d['page']}"] = d
    elif request.method == "DELETE":
        key = request.args.get("key")
        if key in bks:
            del bks[key]
    BOOKMARKS_FILE.write_text(json.dumps(bks), encoding="utf-8")
    return jsonify(bks)


@app.route("/api/uninstall", methods=["POST"])
def uninstall_mag() -> Response | tuple[Response, int]:
    data = request.json
    pdf_filename = data.get("pdf_filename")

    # Find the actual path based on the filename
    target_rel_path = next(
        (f for f in METADATA_CACHE.keys() if f.endswith(pdf_filename)), None
    )
    if not target_rel_path:
        return jsonify({"error": "File not found"}), 404

    pdf_path = DATA_DIR / target_rel_path
    if pdf_path.exists():
        # 1. Find the partner ZIP and text files BEFORE deleting the PDF
        partner_zip = get_partner_zip(target_rel_path)
        loose_texts = list(pdf_path.parent.glob(f"{pdf_path.stem}_p*.txt"))

        # 2. Delete the ZIP
        if partner_zip and partner_zip.exists():
            try:
                os.remove(partner_zip)
            except Exception as e:
                return jsonify({"error": f"Failed to delete ZIP: {e}"}), 500

        # 3. Delete loose text files
        for txt in loose_texts:
            try:
                os.remove(txt)
            except Exception as e:
                return jsonify({"error": f"Failed to delete text file: {e}"}), 500

        # 4. Delete the PDF
        try:
            os.remove(pdf_path)
        except Exception as e:
            return jsonify({"error": f"Failed to delete PDF: {e}"}), 500

        # 5. Clean up the folder if it's now empty
        if pdf_path.parent != DATA_DIR:
            try:
                if not any(pdf_path.parent.iterdir()):
                    pdf_path.parent.rmdir()
            except Exception as e:
                return jsonify({"error": f"Failed to clean up folder: {e}"}), 500

    load_metadata_cache()
    return jsonify({"status": "uninstalled"})


@app.route("/api/ping")
def ping() -> str:
    global LAST_PING
    LAST_PING = time.time()
    return "ok"


@app.route("/")
def index() -> str:
    return render_template_string(HTML_UI)


def monitor_heartbeat() -> None:
    while True:
        time.sleep(5)
        if time.time() - LAST_PING > 20:
            os._exit(0)


if __name__ == "__main__":
    load_metadata_cache()
    threading.Thread(target=monitor_heartbeat, daemon=True).start()
    time.sleep(1)
    webbrowser.open(f"http://127.0.0.1:{SERVER_PORT}")
    app.run(port=SERVER_PORT, debug=False)
