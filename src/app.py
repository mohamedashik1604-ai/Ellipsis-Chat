import os
import io
import re
import json
import base64
import hmac
import html  # NEW: used to safely print the login footer text
import logging
from pathlib import Path
import streamlit as st
import time
from rag import retrieve_context, warm_up, RAG_INSTRUCTIONS
import hashlib
import secrets
import threading
import streamlit.components.v1 as components
from PIL import Image
from groq import Groq, RateLimitError
from db_connection import (
    create_conversation,
    get_chat_history,
    get_user_conversations,
    save_message,
    save_sources,
    authenticate_user,
)

# Imported separately so one missing function doesn't disable the other
try:
    from db_connection import rename_conversation
except ImportError as e:
    logging.warning("rename_conversation not available: %s", e)
    rename_conversation = None

try:
    from db_connection import delete_conversation
except ImportError as e:
    logging.warning("delete_conversation not available: %s", e)
    delete_conversation = None

# Drag-and-drop list: pip install streamlit-sortables
try:
    from streamlit_sortables import sort_items
except ImportError:
    sort_items = None

ASSETS = Path(os.environ.get("ELLIPSIS_ASSETS_DIR", str(Path(__file__).resolve().parent / "assets")))
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")  # NEW


def find_asset(base):  # NEW
    """Return the image at `base`, whether or not the file extension was included."""
    base = Path(base)
    if base.is_file():
        return base
    for ext in IMAGE_EXTS:
        p = base.with_name(base.name + ext)
        if p.is_file():
            return p
    return None


FAVICON = ASSETS / "favicon5.ico"
LOGO_FULL = find_asset(ASSETS / "Company Title") or ASSETS / "Company Title.png"  # CHANGED
TITLE_CAPTION = find_asset(ASSETS / "Title Caption")  # NEW
LOGO_ICON = ASSETS / "Company Icon.png"
AVATAR = str(LOGO_ICON) if LOGO_ICON.exists() else None

# Illustration for the right side of the login page.
# Save your artwork in the assets folder as "Login Art.png" (or .jpg / .jpeg).
LOGIN_ART_CANDIDATES = [ASSETS / f"Login Art{ext}" for ext in (".png", ".jpg", ".jpeg")]

# Pin + custom order are stored here (next to app.py)
PREFS_FILE = Path(__file__).with_name("chat_prefs.json")

st.set_page_config(
    page_title="Ellipsis Chat",
    page_icon=str(FAVICON),
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------- STYLE -----------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:ital,wght@0,400;0,600;1,400&display=swap');

    /* ---------- design tokens ---------- */
    :root {
        --bg: #1b1b19;
        --sidebar: #141413;
        --surface: #242422;
        --surface-2: #2c2c29;
        --border: rgba(255,255,255,0.08);
        --text: #ece9e2;
        --muted: #a8a49b;
        --faint: #77746c;
        --accent: #2FB5A8;
        --danger: #e5786d;
        --serif: 'Source Serif 4', Georgia, 'Times New Roman', serif;
        --col: 780px;              /* width of the chat column and input */
        --avatar: 1.7rem;          /* assistant avatar size */
    }

    html, body, .stApp, .stMarkdown, button p,
    [data-testid="stChatInput"] textarea { font-family: 'Inter', sans-serif; }

    [data-testid="stChatInput"] textarea::placeholder { font-style: italic !important; }

    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        background: var(--bg); color: var(--text);
    }
    ::selection { background: rgba(47,181,168,0.35); }

    /* slim scrollbars everywhere */
    * { scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.14) transparent; }

    /* hide Streamlit chrome */
    #MainMenu, footer, [data-testid="stDecoration"],
    [data-testid="stToolbarActions"], [data-testid="stMainMenu"],
    .stAppDeployButton, [data-testid="stAppDeployButton"] { display: none !important; }
    header[data-testid="stHeader"] { background: transparent; }

    /* button that re-opens a collapsed sidebar */
    [data-testid="stToolbar"],
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapsedControl"] {
        display: flex !important; visibility: visible !important; opacity: 1 !important;
    }
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapsedControl"] button {
        color: var(--muted) !important; background: transparent !important;
        z-index: 999999;
    }
    [data-testid="stExpandSidebarButton"]:hover,
    [data-testid="stSidebarCollapsedControl"] button:hover {
        color: var(--text) !important; background: rgba(255,255,255,0.06) !important;
    }

    /* wider, roomier reading column */
    [data-testid="stMainBlockContainer"] {
        max-width: var(--col) !important; margin: 0 auto;
        padding: 2rem 1.5rem 6rem 1.5rem;
    }

    /* ---------- bottom bar ---------- */
    [data-testid="stBottom"],
    [data-testid="stBottom"] > div { background: var(--bg) !important; }
    [data-testid="stBottom"]::before {
        content: ""; position: absolute; left: 0; right: 0; bottom: 100%;
        height: 2rem; pointer-events: none;
        background: linear-gradient(to top, var(--bg), transparent);
    }
    [data-testid="stBottomBlockContainer"] {
        max-width: var(--col); margin: 0 auto;
        padding: 0 1.5rem 0.25rem 1.5rem;
    }
    [data-testid="stBottomBlockContainer"]::after {
        content: "Ellipsis can make mistakes. Please verify important information.";
        display: block; text-align: center; font-size: 0.72rem;
        color: var(--faint); padding: 0.4rem 0 0.1rem 0;
    }

    /* ---------- input (slim, like the reference) ---------- */
    [data-testid="stChatInput"] {
        border-radius: 20px; border: 1px solid var(--border);
        background: var(--surface); min-height: 0 !important;
        box-shadow: 0 2px 14px rgba(0,0,0,0.22);
        transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    [data-testid="stChatInput"] > div {
        background: transparent !important; align-items: center;
        min-height: 0 !important; padding-top: 0 !important; padding-bottom: 0 !important;
    }
    [data-testid="stChatInput"] textarea {
        background: transparent !important; color: var(--text);
        font-size: 1rem; line-height: 1.5; min-height: 0 !important;
        padding: 0.7rem 0.5rem 0.7rem 1rem !important;
    }
    [data-testid="stChatInput"]:focus-within {
        border-color: rgba(47,181,168,0.55);
        box-shadow: 0 2px 14px rgba(0,0,0,0.22), 0 0 0 3px rgba(47,181,168,0.10);
    }
    [data-testid="stChatInputSubmitButton"] {
        width: 2rem; height: 2rem; min-height: 0; padding: 0;
        margin-right: 0.35rem;
        background: var(--accent) !important; color: #0b1f1d !important;
        border-radius: 10px;
    }
    [data-testid="stChatInputSubmitButton"]:disabled {
        background: rgba(255,255,255,0.08) !important; color: var(--faint) !important;
    }

    /* ---------- sidebar ---------- */
    [data-testid="stSidebar"] {
        background: var(--sidebar); border-right: 1px solid var(--border);
    }
    [data-testid="stSidebarContent"] { background: transparent; padding-top: 0 !important; }
    [data-testid="stSidebarHeader"] {
        position: sticky !important; top: 0; z-index: 60;
        height: 0 !important; min-height: 0 !important; overflow: visible !important;
        padding: 0 !important; margin: 0 !important;
        background: transparent !important;
    }
    /* « button: pinned to the top-right corner, offset applied ONCE */
    [data-testid="stSidebarHeader"] [data-testid="stSidebarCollapseButton"],
    [data-testid="stSidebarHeader"] > button {
        position: absolute; top: 0.5rem; right: -0.4rem; left: auto;
        z-index: 61; width: auto;
    }
    /* the inner button just fills its wrapper */
    [data-testid="stSidebarHeader"] [data-testid="stSidebarCollapseButton"] button {
        position: static;
        color: var(--muted); background: transparent;
    }
    [data-testid="stSidebarHeader"] button:hover {
        color: var(--text); background: rgba(255,255,255,0.06);
    }
    [data-testid="stSidebarUserContent"] {
    padding: 0 0.9rem 0 0.9rem !important;
    }
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0; }

    /* logo: sharp, never clipped, with a clear gap before "New chat" */
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.brand-logo) {
        overflow: visible !important; line-height: 0;
        margin-bottom: 1.6rem !important;
    }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(.brand-logo) {
        overflow: visible !important; line-height: 0;
    }
    .brand-logo {
        display: block; height: auto; max-width: 100%;
        margin: 0; image-rendering: auto;
    }
    .brand-fallback {
        font-family: var(--serif); font-size: 1.4rem; color: var(--text);
        margin: 0;
    }

    /* section labels: above the rows so hover highlights can't cover them */
    .side-label {
        display: block; position: relative; z-index: 2;
        font-size: 0.78rem; font-weight: 500; color: var(--faint);
        padding: 1.3rem 0 0.45rem 0.75rem; margin: 0;
    }

    /* every sidebar button: compact */
    [data-testid="stSidebar"] .stButton > button {
        background: transparent; border: none; box-shadow: none;
        color: var(--muted);
        height: 2rem; min-height: 2rem; padding: 0 0.75rem; border-radius: 8px;
        justify-content: flex-start; text-align: left; font-weight: 400;
        line-height: 1.2;
        transition: background 0.12s ease, color 0.12s ease;
    }
    [data-testid="stSidebar"] .stButton > button div,
    [data-testid="stSidebar"] .stButton > button p {
        justify-content: flex-start; text-align: left; width: 100%;
        font-size: 0.9rem; line-height: 1.2; margin: 0;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(255,255,255,0.05); color: var(--text);
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"],
    [data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] {
        background: rgba(255,255,255,0.08); color: var(--text);
    }

    .st-key-new_chat { margin-top: 1.5rem !important; }

    .st-key-new_chat button {
        background: var(--surface) !important; color: var(--text) !important;
        border: 1px solid var(--border) !important; border-radius: 10px !important;
        font-weight: 500 !important; justify-content: center !important;
        height: 2.4rem !important; min-height: 2.4rem !important;
    }
    .st-key-new_chat button:hover {
        border-color: rgba(47,181,168,0.6) !important;
        background: var(--surface-2) !important;
    }
    .st-key-show_more button { color: var(--faint) !important; font-size: 0.85rem; }

    /* top of the sidebar (logo + New chat): locked while the chat list scrolls.
       Sticky must sit on the element that is a direct child of the tall list,
       so both possible Streamlit structures are covered. */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > *:has(> .st-key-side_top),
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > .st-key-side_top {
        position: sticky; top: 0; z-index: 30;
        background: var(--sidebar);
    }
    .st-key-side_top {
        position: relative;
        background: var(--sidebar);
        padding: 1.3rem 0 0.5rem 0;
    }
    /* soft fade so chats don't cut off sharply under the locked section */
    .st-key-side_top::after {
        content: ""; position: absolute; left: 0; right: 0; top: 100%;
        height: 14px; pointer-events: none;
        background: linear-gradient(to bottom, var(--sidebar), transparent);
    }
    /* soft fade so chats don't cut off sharply under the locked section */
    .st-key-side_top::after {
        content: ""; position: absolute; left: 0; right: 0; top: 100%;
        height: 14px; pointer-events: none;
        background: linear-gradient(to bottom, var(--sidebar), transparent);
    }

    /* log out: pinned to the bottom of the sidebar, chat list scrolls above it */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(> .st-key-logout) {
        min-height: calc(100vh - 1.3rem);
        min-height: calc(100dvh - 1.3rem);
    }
    .st-key-logout {
        position: sticky; bottom: 0; z-index: 20;
        margin-top: auto !important;
        background: var(--sidebar);
        padding: 0.6rem 0 0.9rem 0;
        border-top: 1px solid var(--border);
    }
    .st-key-logout button { color: var(--faint) !important; }
    .st-key-logout button:hover { color: var(--danger) !important; }

    /* ---------- chat rows: tight list, dots float on top ---------- */
    [class*="st-key-row_"] { position: relative; margin: 0; padding: 0; gap: 0; }
    [class*="st-key-row_"] [data-testid="stElementContainer"] { margin: 0; }
    [class*="st-key-row_"] [data-testid="stVerticalBlock"] { gap: 0; }
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {
        gap: 0; flex-wrap: nowrap; align-items: center; position: relative;
    }
    [data-testid="stSidebar"] [data-testid="stColumn"] { min-width: 0; }
    [class*="st-key-row_"] [data-testid="stColumn"]:first-child {
        width: 100% !important; flex: 1 1 100% !important;
    }
    [class*="st-key-row_"] [data-testid="stColumn"]:last-child {
        position: absolute; right: 0.25rem; top: 50%; transform: translateY(-50%);
        width: auto !important; flex: none !important; z-index: 3;
    }

    /* dots only show when you hover the row (always on touch screens) */
    [class*="st-key-row_"] [data-testid="stPopover"] {
        opacity: 0; transition: opacity 0.12s ease; width: auto;
    }
    [class*="st-key-row_"]:hover [data-testid="stPopover"],
    [class*="st-key-row_"] [data-testid="stPopover"]:has(button[aria-expanded="true"]) {
        opacity: 1;
    }
    @media (hover: none) {
        [class*="st-key-row_"] [data-testid="stPopover"] { opacity: 1; }
    }
    [data-testid="stPopover"] > button,
    [data-testid="stPopoverButton"] {
        background: #2a2a28 !important; border: none !important;
        box-shadow: none !important; color: var(--muted);
        height: 1.65rem; min-height: 1.65rem; width: 1.65rem; padding: 0;
        border-radius: 7px; justify-content: center;
    }
    [data-testid="stPopover"] > button:hover,
    [data-testid="stPopoverButton"]:hover {
        background: #353532 !important; color: var(--text);
    }
    /* hide the little chevron Streamlit adds to popover buttons */
    [data-testid="stPopover"] button [data-testid="stIconMaterial"],
    [data-testid="stPopover"] button svg { display: none !important; }

    /* the dropdown itself */
    [data-testid="stPopoverBody"] {
        background: var(--surface); border: 1px solid var(--border);
        border-radius: 12px; padding: 0.35rem; min-width: 11.5rem;
        box-shadow: 0 10px 30px rgba(0,0,0,0.4);
    }
    [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] { gap: 0.1rem; }
    [data-testid="stPopoverBody"] .stButton > button {
        background: transparent; border: none; box-shadow: none;
        color: var(--text); justify-content: flex-start; font-weight: 400;
        height: 2.2rem; min-height: 2.2rem; padding: 0.3rem 0.7rem; border-radius: 8px;
    }
    [data-testid="stPopoverBody"] .stButton > button div,
    [data-testid="stPopoverBody"] .stButton > button p {
        justify-content: flex-start; text-align: left; width: 100%; font-size: 0.9rem;
    }
    [data-testid="stPopoverBody"] .stButton > button:hover {
        background: rgba(255,255,255,0.07);
    }
    [class*="st-key-del_"] button,
    [class*="st-key-del_"] button p { color: var(--danger) !important; }

    /* inline rename form */
    [data-testid="stSidebar"] [data-testid="stForm"] {
        border: none !important; padding: 0 !important;
    }
    [data-testid="stSidebar"] [data-testid="stFormSubmitButton"] > button {
        height: 2rem; min-height: 2rem; border-radius: 8px;
    }

    /* ---------- welcome screen ---------- */
    .welcome { text-align: center; margin: 12vh 0 2.4rem 0; }
    .welcome h1 {
        font-family: var(--serif) !important; font-size: 2.8rem;
        font-weight: 400; letter-spacing: -0.01em; line-height: 1.15;
        padding: 0; margin: 0; color: var(--text);
    }
    [data-testid="stMain"] .stButton > button {
        min-height: 3.8rem; border-radius: 14px; padding: 0.8rem 1.2rem;
        border: 1px solid var(--border); background: var(--surface);
        color: var(--muted);
        justify-content: flex-start; text-align: left;
        transition: all 0.15s ease;
    }
    [data-testid="stMain"] .stButton > button div,
    [data-testid="stMain"] .stButton > button p {
        justify-content: flex-start; text-align: left; font-size: 0.95rem;
    }
    [data-testid="stMain"] .stButton > button:hover {
        border-color: rgba(47,181,168,0.55); background: var(--surface-2);
        color: var(--text); transform: translateY(-1px);
    }

    /* ---------- messages ---------- */
    [data-testid="stChatMessage"] {
        background: transparent; padding: 0.5rem 0; gap: 0.8rem;
    }
    [data-testid="stChatMessageContent"] [data-testid="stVerticalBlock"] { gap: 0.4rem; }

    /* assistant avatar: small and tidy, aligned with the first line of text */
    [data-testid="stChatMessageAvatarAssistant"] {
        display: flex !important;
        width: var(--avatar) !important; height: var(--avatar) !important;
        min-width: var(--avatar) !important; min-height: var(--avatar) !important;
        background: transparent !important; border-radius: 8px;
        margin-top: 0.3rem; overflow: hidden;
    }
    [data-testid="stChatMessageAvatarAssistant"] img {
        width: 100% !important; height: 100% !important; object-fit: contain;
    }

    /* user: right-aligned bubble, no avatar */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        flex-direction: row-reverse; padding: 0.6rem 0;
    }
    [data-testid="stChatMessageAvatarUser"] { display: none !important; }
    /* user message: the text container itself is the bubble */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
    [data-testid="stChatMessageContent"] {
        flex: 1 1 auto; min-width: 0;
        background: transparent; padding: 0;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
    [data-testid="stMarkdownContainer"] {
        box-sizing: border-box; width: fit-content; max-width: 85%;
        margin-left: auto;
        background: var(--surface-2); border-radius: 18px;
        padding: 0.6rem 1rem; font-size: 1rem; line-height: 1.5;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
    [data-testid="stMarkdownContainer"] p {
        margin: 0 0 0.5rem 0 !important; line-height: 1.5; overflow-wrap: anywhere;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
    [data-testid="stMarkdownContainer"] p:last-child { margin-bottom: 0 !important; }

    /* assistant: content fills the column */
    [data-testid="stChatMessage"]:not(:has([data-testid="stChatMessageAvatarUser"]))
    [data-testid="stChatMessageContent"] { flex: 1 1 auto; min-width: 0; }

        /* assistant replies: serif, compact, easy to read */
    [data-testid="stChatMessage"]:not(:has([data-testid="stChatMessageAvatarUser"]))
    [data-testid="stMarkdownContainer"] :is(p, li, blockquote, h1, h2, h3, h4) {
        font-family: var(--serif);
        font-kerning: normal; text-rendering: optimizeLegibility;
        word-spacing: 0.06em;
    }
    [data-testid="stChatMessage"]:not(:has([data-testid="stChatMessageAvatarUser"]))
    [data-testid="stMarkdownContainer"] :is(p, li) {
        font-size: 1rem; line-height: 1.55; color: var(--text);
    }
    [data-testid="stMarkdownContainer"] p { margin: 0 0 0.6rem 0; }
    [data-testid="stMarkdownContainer"] p:last-child { margin-bottom: 0; }
    [data-testid="stMarkdownContainer"] :is(ul, ol) {
        padding-left: 1.35rem; margin: 0 0 0.6rem 0;
    }
    [data-testid="stMarkdownContainer"] li { margin: 0.1rem 0; }
    [data-testid="stMarkdownContainer"] li > p { margin: 0; }
    [data-testid="stMarkdownContainer"] li::marker { color: var(--faint); }
    [data-testid="stMarkdownContainer"] strong { font-weight: 600; }
    [data-testid="stMarkdownContainer"] h1 { font-size: 1.3rem; font-weight: 600; padding: 0.4rem 0 0.1rem; }
    [data-testid="stMarkdownContainer"] h2 { font-size: 1.18rem; font-weight: 600; padding: 0.4rem 0 0.1rem; }
    [data-testid="stMarkdownContainer"] h3 { font-size: 1.06rem; font-weight: 600; padding: 0.3rem 0 0.1rem; }
    [data-testid="stMarkdownContainer"] a { color: var(--accent); }
    [data-testid="stMarkdownContainer"] hr { border-color: var(--border); margin: 1.4rem 0; }
    [data-testid="stMarkdownContainer"] blockquote {
        border-left: 3px solid var(--border); color: var(--muted);
        margin: 0 0 1rem 0; padding-left: 1rem;
    }
    [data-testid="stHeaderActionElements"] { display: none !important; }

    /* tables */
    [data-testid="stMarkdownContainer"] table {
        border-collapse: separate; border-spacing: 0; width: 100%;
        border: 1px solid var(--border); border-radius: 12px; overflow: hidden;
        font-family: 'Inter', sans-serif; font-size: 0.92rem; margin: 0.5rem 0 1.2rem 0;
    }
    [data-testid="stMarkdownContainer"] th,
    [data-testid="stMarkdownContainer"] td {
        border: none !important; border-bottom: 1px solid var(--border) !important;
        padding: 0.75rem 1rem; text-align: left; vertical-align: top;
    }
    [data-testid="stMarkdownContainer"] th { background: var(--surface); font-weight: 600; }
    [data-testid="stMarkdownContainer"] tr:last-child td { border-bottom: none !important; }

    /* code */
    [data-testid="stCode"],
    [data-testid="stMarkdownContainer"] pre {
        background: #121211 !important; border: 1px solid var(--border);
        border-radius: 12px;
    }
    [data-testid="stMarkdownContainer"] code {
        font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
        font-size: 0.9rem;
    }
    [data-testid="stMarkdownContainer"] :not(pre) > code {
        background: rgba(255,255,255,0.08); color: #7fd8cd;
        padding: 0.12rem 0.4rem; border-radius: 6px;
    }
        /* breathing room above the "Retrieved using ..." label */
    [data-testid="stChatMessage"] [data-testid="stCaptionContainer"] {
        margin-top: 0.9rem;
        padding-left: 0.1rem;
    }
    [data-testid="stChatMessage"] [data-testid="stCaptionContainer"] p {
        font-size: 0.78rem;
        color: var(--faint);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Extra CSS that is only injected on the login page (overrides the dark chat theme)
LOGIN_CSS = """
<style>
/* no sidebar, no chat chrome, edge-to-edge split screen */
[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"],
[data-testid="stBottom"] { display: none !important; }

.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: #d6cdbd !important; }
[data-testid="stMainBlockContainer"] { max-width: 100% !important; padding: 0 !important; }
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] { gap: 0 !important; }
[data-testid="stMain"] [data-testid="stHorizontalBlock"] { gap: 0 !important; align-items: stretch; }
[data-testid="stMain"] [data-testid="stColumn"] { padding: 0 !important; }

/* left: form panel  (CHANGED: three stacked groups -> brand on top, form in the
   middle, footer at the bottom) */
.st-key-login_left {
    background-color: #d6cdbd;
    background-image:
        url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.35  0 0 0 0 0.28  0 0 0 0 0.2  0 0 0 0.10 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>"),
        linear-gradient(180deg, #d9d0c1 0%, #cfc5b4 100%);
    min-height: 100vh; height: 100%;
    display: flex; flex-direction: column;
    justify-content: space-between; align-items: stretch;
    padding: 0 0 1.2rem 0; gap: 0 !important;
}
.st-key-login_top,
.st-key-login_mid,
.st-key-login_bottom { width: 100%; gap: 0 !important; }

/* soft shadow that follows the banner's pointed shape */
.st-key-login_top { filter: drop-shadow(0 8px 12px rgba(40, 25, 10, 0.30)); }

.st-key-login_mid {
    width: calc(100% - 3rem); max-width: 380px;
    margin: 0 auto; padding-bottom: 4rem;
}
.st-key-login_bottom { box-sizing: border-box; padding: 0 1.5rem; }

/* NEW: company title + caption */
.login-brand {
    display: flex; flex-direction: column; align-items: center;
    gap: 1rem; text-align: center; line-height: 0;
}

/* dark banner: flush to the top, pointed V at the bottom */
.st-key-login_top .login-brand {
    background: linear-gradient(180deg, #2a241e 0%, #1f1b17 100%);
    padding: 3rem 1.6rem calc(1.8rem + 40px) 1.6rem;
    clip-path: polygon(0 0, 100% 0, 100% calc(100% - 40px),
                       50% 100%, 0 calc(100% - 40px));
}

.login-title-img,
.login-caption-img {
    display: block; height: auto; max-width: 100%; margin: 0 auto;
}
.login-brand-fallback {
    font-family: 'Source Serif 4', Georgia, serif; font-size: 2.6rem;
    color: #8a6a4b; line-height: 1.1;
}
.login-caption-fallback {
    font-family: 'Source Serif 4', Georgia, serif; font-style: italic;
    font-size: 1.15rem; color: #8a5a3c; line-height: 1.3;
}

/* NEW: footer */
.login-footer {
    border-top: 1px solid #222222; padding-top: 0.6rem;
    font-family: 'Times New Roman', Times, serif;
    font-size: clamp(0.7rem, calc((40vw - 3.5rem) / 56), 0.85rem);
    line-height: 1.4; color: #111111; text-align: center;
    white-space: nowrap;
}
@media (max-width: 1400px) {
    .login-footer { white-space: normal; }  /* narrow screens: allow wrapping */
}

.login-title {
    font-family: 'Inter', sans-serif; font-size: 1.5rem; font-weight: 400;
    color: #3a4249; text-align: center; margin: 0 0 1.8rem 0; letter-spacing: -0.005em;
}

/* inputs */
.st-key-login_left [data-testid="stForm"] { border: none !important; padding: 0 !important; background: transparent; }
.st-key-login_left [data-testid="stForm"] [data-testid="stVerticalBlock"] { gap: 1rem; }
/* input boxes: explicit dark background + light text */
.st-key-login_left [data-baseweb="input"],
.st-key-login_left [data-baseweb="base-input"] {
    background: #222222 !important; border-radius: 10px !important;
}
.st-key-login_left input {
    background: #222222 !important;
    color: #f1ece3 !important; -webkit-text-fill-color: #f1ece3 !important;
    caret-color: #f1ece3;
    font-size: 1rem; padding-left: 1rem !important;
}
.st-key-login_left input::placeholder {
    color: #8a939b !important; -webkit-text-fill-color: #8a939b !important;
}
/* browser autofill (saved passwords) would otherwise turn the box white/yellow */
.st-key-login_left input:-webkit-autofill,
.st-key-login_left input:-webkit-autofill:focus {
    -webkit-box-shadow: 0 0 0 1000px #222222 inset !important;
    -webkit-text-fill-color: #f1ece3 !important;
}

/* hide "Press Enter to submit form" */
.st-key-login_left [data-testid="InputInstructions"] { display: none !important; }
.st-key-login_left [data-baseweb="input"] {
    border: 1px solid #cfd5da !important; min-height: 3rem;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.st-key-login_left [data-baseweb="input"]:focus-within {
    border-color: #245f70 !important; box-shadow: 0 0 0 3px rgba(36,95,112,0.15);
}
.st-key-login_left [data-baseweb="input"] button,
.st-key-login_left [data-baseweb="input"] svg { color: #7b858d !important; fill: #7b858d !important; background: transparent !important; }

/* submit */
.st-key-login_left [data-testid="stFormSubmitButton"] button {
    background: #245f70 !important; color: #ffffff !important;
    border: none !important; border-radius: 10px !important;
    height: 3rem; min-height: 3rem; font-weight: 500; font-size: 1rem;
    box-shadow: 0 1px 2px rgba(0,0,0,0.12);
    justify-content: center !important; transform: none !important;
    transition: background 0.15s ease;
}
.st-key-login_left [data-testid="stFormSubmitButton"] button:hover { background: #1d4d5b !important; }
.st-key-login_left [data-testid="stFormSubmitButton"] button p { color: #ffffff !important; justify-content: center !important; }

/* error message */
.st-key-login_left [data-testid="stAlert"] { border-radius: 10px; margin-top: 0.9rem; }

/* right: artwork */
.login-art {
    height: 100vh; width: 100%;
    background-size: cover; background-position: center; background-repeat: no-repeat;
}
.login-art.fallback {
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, #1b1b19 0%, #243a3d 100%);
}
.login-art.fallback img { width: 220px; height: auto; }

@media (max-width: 800px) {
    .login-art { display: none; }
    .st-key-login_left { padding: 0 0 1rem 0; }
    .st-key-login_mid { padding-bottom: 2rem; }
}
</style>
"""

# ----------------- CONFIG -----------------
@st.cache_resource(show_spinner=False)
def get_client():
    return Groq(api_key=os.environ.get("GROQ_API_KEY"), timeout=60)


client = get_client()


@st.cache_resource(show_spinner=False)
def _start_warm_up():
    """Runs once per server start, in the background, so the first user isn't the one waiting."""
    threading.Thread(target=warm_up, daemon=True).start()
    return True


_start_warm_up()


@st.cache_resource(show_spinner=False)
def _prefs_lock():
    return threading.Lock()

MODEL = "qwen/qwen3.8-27b"
# Small, fast, non-reasoning model used only for generating chat titles
TITLE_MODEL = "qwen/qwen3.8-27b"
CONTEXT_LIMIT = 20  # last N messages sent to the model
LOGO_WIDTH = 80  # logo width in CSS pixels

# NEW: login page branding (widths are in CSS pixels)
LOGIN_TITLE_WIDTH = 290      # "Company Title" image
LOGIN_CAPTION_WIDTH = 380    # "Title Caption" image
LOGIN_CAPTION_TEXT = "A silly chatbot with a serious pipeline."  # used if the image is missing
LOGIN_FOOTER = (
    "Ellipsis answers from the NCATS Assay Guidance Manual using vector search. "
    "It can make mistakes, so check the cited pages for anything important."
)

# Users are checked against the app_users table (see db_connection.authenticate_user).

# Stay-logged-in cookie
AUTH_COOKIE = "ellipsis_auth"
SESSION_DAYS = 7
@st.cache_resource(show_spinner=False)
def _temp_secret():
    return secrets.token_hex(32)


# Never commit an authentication signing key to source control.
# Set APP_SECRET in the host environment; use a stable random key in production.
SECRET_KEY = os.environ.get("APP_SECRET", "").strip()
if len(SECRET_KEY) < 32:
    raise RuntimeError("APP_SECRET is required and must be at least 32 characters long")

# Set COOKIE_SECURE=false only when developing over local plain HTTP.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").strip().lower() not in {"false", "0", "no"}

SYSTEM_PROMPT = (
    "You are Ellipsis, an AI assistant for assay development and screening scientists, "
    "answering from the NCATS Assay Guidance Manual. "
    "Never claim to be ChatGPT or made by OpenAI or any others. "
    "Only mention your name if the user asks who you are; never open a reply with "
    "a greeting or a self-introduction. "
    "Answer first, then stop. Match the length of your reply to the question: a simple "
    "or factual question gets one or two sentences, and a request to explain, describe, "
    "compare or walk through a method gets a fuller answer. Do not add information the "
    "question did not ask for. "
    "Write in clear, natural prose; use a short list or numbered steps only when the content "
    "is a sequence or a set of parallel items. Understand the conversation's context before "
    "answering. Do not give medical, clinical or safety-critical advice."
)

TITLE_PROMPT = (
    "You are Ellipsis, an AI assistant. Never claim to be ChatGPT or made by OpenAI or any others. "
    "You write short titles for chat conversations. Given the user's first "
    "message, reply with a concise title of 3 to 6 words that captures the "
    "topic. Use the same language as the message. Do not use quotation marks, "
    "emojis, or a trailing period. Do not answer the message. "
    "Reply with the title only."
)

# Look of the drag-and-drop lists inside the "Reorder chats" dialog
SORT_STYLE = """
.sortable-component { background: transparent; border: none; padding: 0; }
.sortable-container { background: #242422; border-radius: 12px; padding: 6px; margin-bottom: 12px; }
.sortable-container-header { color: #a8a49b; font-size: 12px; padding: 6px 8px; background: transparent; }
.sortable-container-body { background: transparent; }
.sortable-item { background: #2c2c29; color: #ece9e2; border-radius: 8px; padding: 8px 12px;
                 margin: 4px 0; font-size: 14px; cursor: grab; }
.sortable-item:hover { background: #34342f; }
"""


# ----------------- HELPERS -----------------
@st.cache_resource(show_spinner=False)
def logo_html(path, display_width, css_class="brand-logo", alt="Ellipsis"):  # CHANGED: class + alt are now parameters
    """
    Embed the logo as a lossless, high-resolution PNG and let the browser shrink it.
    st.image(width=...) shrinks the file to the display size on the server, which
    looks blurry on high-DPI screens. Here the image is kept at 4x the display size.
    (Restart the app after replacing the logo file; the result is cached.)
    """
    try:
        img = Image.open(path)
        # trim transparent padding so the logo lines up with the sidebar edge
        img = img.convert("RGBA")
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        target = display_width * 4
        if img.width > target:
            new_h = round(img.height * target / img.width)
            img = img.resize((target, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return (
            f'<img class="{css_class}" alt="{html.escape(alt)}" '
            f'style="width:{display_width}px" '
            f'src="data:image/png;base64,{b64}">'
        )
    except Exception:
        logging.exception("Could not load logo")
        return '<div class="brand-fallback">Ellipsis</div>'


@st.cache_resource(show_spinner=False)
def login_header_html():  # NEW
    """Company title + caption shown at the top of the login panel (cached)."""
    if LOGO_FULL.exists():
        title = logo_html(str(LOGO_FULL), LOGIN_TITLE_WIDTH, "login-title-img", "Ellipsis")
    else:
        logging.warning("Company Title image not found in %s", ASSETS)
        title = '<div class="login-brand-fallback">Ellipsis</div>'

    if TITLE_CAPTION is not None and TITLE_CAPTION.exists():
        caption = logo_html(
            str(TITLE_CAPTION), LOGIN_CAPTION_WIDTH, "login-caption-img", LOGIN_CAPTION_TEXT
        )
    else:
        logging.warning("Title Caption image not found in %s", ASSETS)
        caption = f'<div class="login-caption-fallback">{html.escape(LOGIN_CAPTION_TEXT)}</div>'

    return f'<div class="login-brand">{title}{caption}</div>'


@st.cache_resource(show_spinner=False)
def login_art_html():
    """Right-hand login artwork, embedded as a high-quality JPEG (cached)."""
    for p in LOGIN_ART_CANDIDATES:
        if p.exists():
            try:
                img = Image.open(p).convert("RGB")
                img.thumbnail((2000, 2000), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88, optimize=True)
                b64 = base64.b64encode(buf.getvalue()).decode()
                return (
                    f'<div class="login-art" '
                    f'style="background-image:url(data:image/jpeg;base64,{b64})"></div>'
                )
            except Exception:
                logging.exception("Could not load login artwork")
    # fallback when no artwork file exists: dark panel with the company logo
    logo = logo_html(str(LOGO_FULL), 220)
    return f'<div class="login-art fallback">{logo}</div>'


def _sign(user_id, expiry):
    return hmac.new(
        SECRET_KEY.encode(), f"{user_id}|{expiry}".encode(), hashlib.sha256
    ).hexdigest()


def make_token(user_id):
    """Signed token: '<user_id>.<expiry>.<signature>'. Can't be forged without SECRET_KEY."""
    expiry = int(time.time()) + SESSION_DAYS * 86400
    return f"{user_id}.{expiry}.{_sign(user_id, expiry)}"


def token_user(token):
    """The user id inside a valid, unexpired token, else None."""
    try:
        user_id, expiry, sig = token.split(".", 2)
        if int(expiry) < time.time():
            return None
        if hmac.compare_digest(sig, _sign(int(user_id), int(expiry))):
            return int(user_id)
    except Exception:
        pass
    return None


def _uid():
    """The logged-in user's id."""
    return st.session_state.user_id


def write_cookie(value, max_age):
    """Streamlit can't set cookies directly, so a tiny script in a hidden frame does it."""
    components.html(
        f"<script>window.parent.document.cookie = "
        f"'{AUTH_COOKIE}={value}; max-age={max_age}; path=/; SameSite=Lax"
        f"{'; Secure' if COOKIE_SECURE else ''}';</script>",
        height=0,
    )


def login_page():
    """Static split-screen login. Stops the script until the user signs in."""
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)
    left, right = st.columns([2, 3], gap="small")

    with left:
        with st.container(key="login_left"):
            # NEW: company title + caption (top)
            with st.container(key="login_top"):
                st.markdown(login_header_html(), unsafe_allow_html=True)

            # login form (middle)
            with st.container(key="login_mid"):
                st.markdown(
                    '<div class="login-title">Enter your Login Credentials</div>',
                    unsafe_allow_html=True,
                )
                with st.form("login_form", border=False):
                    username = st.text_input(
                        "Username", placeholder="Username", label_visibility="collapsed"
                    )
                    password = st.text_input(
                        "Password", placeholder="Password", type="password",
                        label_visibility="collapsed",
                    )
                    submitted = st.form_submit_button("Submit", use_container_width=True)

                if submitted:
                    try:
                        user_id = authenticate_user(username, password)
                    except Exception:
                        logging.exception("Login lookup failed")
                        st.error("Couldn't reach the login service. Please try again.")
                        user_id = None
                    else:
                        if user_id is None:
                            st.error("Incorrect username or password.")
                    if user_id is not None:
                        st.session_state.authenticated = True
                        st.session_state.user_id = user_id
                        st.session_state.set_cookie_token = make_token(user_id)
                        st.rerun()

            # NEW: footer (bottom)
            with st.container(key="login_bottom"):
                st.markdown(
                    f'<div class="login-footer">{html.escape(LOGIN_FOOTER)}</div>',
                    unsafe_allow_html=True,
                )

                # remove the saved login cookie after logging out
                if st.session_state.pop("clear_cookie", False):
                    write_cookie("", 0)

    with right:
        st.markdown(login_art_html(), unsafe_allow_html=True)

    st.stop()

def clean_text(s):
    """Swap thin/non-breaking spaces (they render cramped in this font) for normal spaces."""
    return (s or "").replace("\u202f", " ").replace("\u00a0", " ").replace("\u2009", " ")

MARK_RE = re.compile(r"\n*\[RETRIEVED: ([^\]]+)\]\s*$")


def split_marker(text):
    """Return (text without the trailing marker, label or None)."""
    text = text or ""
    m = MARK_RE.search(text)
    if m:
        return text[:m.start()].rstrip(), m.group(1)
    return text, None

def _collect(pieces, out):
    """Passes streamed text through unchanged and keeps a copy in `out` for the database."""
    for p in pieces:
        if p:
            out.append(p)
            yield p


def make_title(text, limit=40):
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def generate_title(text, limit=50):
    """Ask a small model for a short title; fall back to truncation on any failure."""
    try:
        resp = client.chat.completions.create(
            model=TITLE_MODEL,
            messages=[
                {"role": "system", "content": TITLE_PROMPT},
                {"role": "user", "content": text[:1000]},
            ],
            max_tokens=300,
            temperature=0.3,
            extra_body={"reasoning_effort": "low"},
        )
        title = (resp.choices[0].message.content or "").strip()
        title = title.splitlines()[0] if title else ""
        title = title.strip(" \"'“”‘’`*#").rstrip(".")
        if title:
            return make_title(title, limit=limit)
    except Exception:
        logging.exception("Title generation failed")
    return make_title(text)

def chat_label(convo):
    title = (convo.get("title") or "").strip()
    if title in ("", "New Chat", "Web Chat PoC"):
        return f"Untitled · #{convo['id']}"
    return title


# ---- pin / order preferences (stored in chat_prefs.json) ----
def load_prefs():
    try:
        data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
        p = data.get(str(_uid()), {})
    except Exception:
        p = {}
    return {"pinned": list(p.get("pinned", [])), "order": list(p.get("order", []))}


def save_prefs(prefs):
    with _prefs_lock():                      # several users share this file
        try:
            data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data[str(_uid())] = prefs
        tmp = PREFS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, PREFS_FILE)


def split_convos(convos, prefs):
    """Return (pinned, recents). New chats go on top of recents; saved order is kept."""
    by_id = {c["id"]: c for c in convos}
    pinned = [by_id[i] for i in prefs["pinned"] if i in by_id]
    pinned_ids = {c["id"] for c in pinned}
    rest = [c for c in convos if c["id"] not in pinned_ids]
    rank = {i: n for n, i in enumerate(prefs["order"])}
    fresh = [c for c in rest if c["id"] not in rank]
    ranked = sorted((c for c in rest if c["id"] in rank), key=lambda c: rank[c["id"]])
    return pinned, fresh + ranked


def toggle_pin(convo_id):
    p = load_prefs()
    if convo_id in p["pinned"]:
        p["pinned"].remove(convo_id)
    else:
        p["pinned"].insert(0, convo_id)
    save_prefs(p)


# ---- dialogs ----
@st.dialog("Delete chat?")
def confirm_delete(convo_id, title):
    st.write(f"“{title}” will be permanently deleted. This can't be undone.")
    a, b = st.columns(2)
    if a.button("Cancel", use_container_width=True):
        st.rerun()
    if b.button("Delete", type="primary", use_container_width=True):
        if delete_conversation is None:
            st.error("Add delete_conversation() to db_connection.py first.")
            return
        try:
            ok = delete_conversation(convo_id, user_id=_uid())
        except Exception:
            logging.exception("Delete failed")
            st.error("Couldn't delete this chat. Please try again.")
            return
        if not ok:
            st.error("Couldn't delete this chat. It may no longer exist.")
            return
        p = load_prefs()
        p["pinned"] = [i for i in p["pinned"] if i != convo_id]
        p["order"] = [i for i in p["order"] if i != convo_id]
        save_prefs(p)
        if st.session_state.conversation_id == convo_id:
            st.session_state.conversation_id = None
        st.rerun()


@st.dialog("Reorder chats")
def reorder_dialog():
    if sort_items is None:
        st.warning("Drag and drop needs one extra package:")
        st.code("pip install streamlit-sortables", language="bash")
        return

    prefs = load_prefs()
    pinned, recents = split_convos(get_user_conversations(_uid()), prefs)

    label_to_id = {}

    def unique_label(c):
        base = chat_label(c)
        label, n = base, 2
        while label in label_to_id:
            label = f"{base} ({n})"
            n += 1
        label_to_id[label] = c["id"]
        return label

    containers = [
        {"header": "Pinned", "items": [unique_label(c) for c in pinned]},
        {"header": "Recents", "items": [unique_label(c) for c in recents]},
    ]
    st.caption("Drag chats to reorder them. Drop one into Pinned to pin it.")
    result = sort_items(
        containers, multi_containers=True, custom_style=SORT_STYLE, key="reorder_sortable"
    )

    if st.button("Save order", type="primary", use_container_width=True):
        save_prefs(
            {
                "pinned": [label_to_id[l] for l in result[0]["items"]],
                "order": [label_to_id[l] for l in result[1]["items"]],
            }
        )
        st.rerun()


# ---- one row in the sidebar ----
def render_row(c, is_pinned):
    cid_ = c["id"]
    title = chat_label(c)
    is_active = cid_ == st.session_state.conversation_id

    with st.container(key=f"row_{cid_}"):
        # inline rename mode
        if st.session_state.get("renaming") == cid_:
            with st.form(f"rename_form_{cid_}"):
                new_title = st.text_input(
                    "Rename chat",
                    value=(c.get("title") or ""),
                    label_visibility="collapsed",
                )
                b1, b2 = st.columns(2)
                save = b1.form_submit_button("Save", use_container_width=True)
                cancel = b2.form_submit_button("Cancel", use_container_width=True)
            if save and new_title.strip():
                if rename_conversation is None:
                    st.toast("Add rename_conversation() to db_connection.py first.")
                else:
                    try:
                        ok = rename_conversation(
                            cid_, make_title(new_title, limit=60), user_id=_uid()
                        )
                        if not ok:
                            st.toast("Couldn't rename this chat.")
                    except Exception:
                        logging.exception("Rename failed")
                        st.toast("Couldn't rename this chat.")
            if save or cancel:
                st.session_state.renaming = None
                st.rerun()
            return

        left, right = st.columns([0.85, 0.15], gap="small", vertical_alignment="center")
        with left:
            if st.button(
                title,
                key=f"convo_{cid_}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state.conversation_id = cid_
                st.rerun()
        with right:
            with st.popover("⋮"):
                if st.button(
                    "Unpin" if is_pinned else "Pin",
                    icon=":material/push_pin:",
                    key=f"pin_{cid_}",
                    use_container_width=True,
                ):
                    toggle_pin(cid_)
                    st.rerun()
                if st.button(
                    "Rename",
                    icon=":material/edit:",
                    key=f"ren_{cid_}",
                    use_container_width=True,
                ):
                    st.session_state.renaming = cid_
                    st.rerun()
                if st.button(
                    "Reorder",
                    icon=":material/drag_indicator:",
                    key=f"ord_{cid_}",
                    use_container_width=True,
                ):
                    reorder_dialog()
                if st.button(
                    "Delete",
                    icon=":material/delete:",
                    key=f"del_{cid_}",
                    use_container_width=True,
                ):
                    confirm_delete(cid_, title)

def set_browser_title(title: str):
    """Dynamically set and lock the browser tab title against Streamlit resets."""
    escaped_title = json.dumps(title)
    components.html(
        f"""
        <script>
        (function() {{
            const targetTitle = {escaped_title};
            const doc = window.parent.document;
            doc.title = targetTitle;

            // Clear any previously registered title lock
            if (window.parent.__titleLockTimer) {{
                clearInterval(window.parent.__titleLockTimer);
            }}

            // Re-enforce targetTitle if Streamlit resets it back to default on interactions
            window.parent.__titleLockTimer = setInterval(() => {{
                if (doc.title !== targetTitle) {{
                    doc.title = targetTitle;
                }}
            }}, 1000);
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


# ----------------- SESSION STATE + LOGIN GATE -----------------
# conversation_id = None means a fresh chat that isn't saved until the first message
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "renaming" not in st.session_state:
    st.session_state.renaming = None
if "authenticated" not in st.session_state:
    # a refresh starts a new session, so check the saved cookie once
    try:
        saved = st.context.cookies.get(AUTH_COOKIE, "")
    except Exception:
        saved = ""
    st.session_state.user_id = token_user(saved)
    st.session_state.authenticated = st.session_state.user_id is not None

# Nothing below this line runs until the user has signed in
if not st.session_state.authenticated:
    login_page()

# ---- Inactivity check (10 mins) & Chat URL sync ----
INACTIVITY_TIMEOUT = 600  # 10 minutes in seconds
now = time.time()

# Check last active timestamp
last_active = st.session_state.get("last_active_time")

# If the session was idle for more than 10 minutes, reset to a fresh chat
if last_active and (now - last_active > INACTIVITY_TIMEOUT):
    st.session_state.conversation_id = None
    if "chat" in st.query_params:
        del st.query_params["chat"]

# Update activity timestamp
st.session_state.last_active_time = now

# Restore chat from URL only on short refreshes / valid active sessions
if "chat_restored" not in st.session_state:
    st.session_state.chat_restored = True
    raw = st.query_params.get("chat")
    if raw and st.session_state.conversation_id is None:
        try:
            wanted = int(raw)
            if any(c["id"] == wanted for c in get_user_conversations(_uid())):
                st.session_state.conversation_id = wanted
        except (ValueError, TypeError):
            pass

_open_chat = st.session_state.conversation_id
if _open_chat:
    if st.query_params.get("chat") != str(_open_chat):
        st.query_params["chat"] = str(_open_chat)
elif "chat" in st.query_params:
    del st.query_params["chat"]

# ----------------- DATA PREPARATION -----------------
user_convos = get_user_conversations(_uid())

# ----------------- SIDEBAR -----------------
with st.sidebar:
    with st.container(key="side_top"):
        st.markdown(logo_html(str(LOGO_FULL), LOGO_WIDTH), unsafe_allow_html=True)

        if st.button("New chat", icon=":material/add:", key="new_chat", use_container_width=True):
            st.session_state.conversation_id = None
            st.rerun()

    prefs = load_prefs()
    pinned_convos, recent_convos = split_convos(user_convos, prefs)
    shown = st.session_state.get("shown", 12)

    if pinned_convos:
        st.markdown('<div class="side-label">Pinned</div>', unsafe_allow_html=True)
        for c in pinned_convos:
            render_row(c, is_pinned=True)

    st.markdown('<div class="side-label">Recents</div>', unsafe_allow_html=True)
    for c in recent_convos[:shown]:
        render_row(c, is_pinned=False)

    if len(recent_convos) > shown:
        if st.button("Show more", icon=":material/expand_more:", key="show_more",
                     use_container_width=True):
            st.session_state.shown = shown + 15
            st.rerun()

    if st.button("Log out", icon=":material/logout:", key="logout", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_id = None
        st.session_state.conversation_id = None
        st.session_state.renaming = None
        st.session_state.clear_cookie = True
        if "chat" in st.query_params:
            del st.query_params["chat"]
        st.rerun()

    # write the login cookie once, right after signing in
    _token = st.session_state.pop("set_cookie_token", None)
    if _token:
        write_cookie(_token, SESSION_DAYS * 86400)

# ----------------- MAIN -----------------
cid = st.session_state.conversation_id
# Do not read a conversation that does not belong to the signed-in user.
if cid is not None and not any(c["id"] == cid for c in get_user_conversations(_uid())):
    cid = None
    st.session_state.conversation_id = None
    if "chat" in st.query_params:
        del st.query_params["chat"]
messages = get_chat_history(cid) if cid else []

# Dynamically set browser tab title
current_title = "Ellipsis Chat"
if cid:
    # Find active conversation title
    convo = next((c for c in user_convos if c["id"] == cid), None)
    if convo and convo.get("title"):
        label = chat_label(convo)
        # Avoid setting raw fallback placeholders
        if not label.startswith("Untitled ·"):
            current_title = f"{label} - Ellipsis"

set_browser_title(current_title)

# Clear the query param in the browser when the tab is closed or idle > 10 mins
components.html(
    """
    <script>
    const P = window.parent;
    const IDLE_LIMIT = 10 * 60 * 1000; // 10 minutes
    const now = Date.now();
    const lastActive = parseInt(P.localStorage.getItem("ellipsis_last_seen") || "0", 10);

    // If reopening after > 10 minutes, strip ?chat= so it opens as a fresh chat
    if (lastActive && (now - lastActive > IDLE_LIMIT)) {
        const url = new URL(P.location.href);
        if (url.searchParams.has("chat")) {
            url.searchParams.delete("chat");
            P.history.replaceState(null, "", url.pathname);
            P.location.reload();
        }
    }

    // Keep updating the activity heartbeat
    P.localStorage.setItem("ellipsis_last_seen", now.toString());
    P.addEventListener("mousemove", () => P.localStorage.setItem("ellipsis_last_seen", Date.now().toString()));
    P.addEventListener("keydown", () => P.localStorage.setItem("ellipsis_last_seen", Date.now().toString()));
    </script>
    """,
    height=0,
    width=0,
)

# Show custom greeting only on an empty chat before the first message
placeholder = "What's the matter, darlo?" if not messages else "Write a message..."
typed = st.chat_input(placeholder)
prompt = typed or st.session_state.pop("pending_prompt", None)

# Welcome screen for empty chats
if not messages and not prompt:
    st.markdown(
        '<div class="welcome"><h1>How can I help you today?</h1></div>',
        unsafe_allow_html=True,
    )

# Existing messages
for msg in messages:
    avatar = AVATAR if msg["role"] == "assistant" else None
    with st.chat_message(msg["role"], avatar=avatar):
        body, label = split_marker(msg["content"])
        st.markdown(clean_text(body))
        if label:
            st.caption("📖 From the manual: " + label)
FIT_JS = """
<script>
const P = window.parent, doc = P.document;
function textWidth(el) {
  const tw = doc.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let min = Infinity, max = -Infinity, n;
  while ((n = tw.nextNode())) {
    if (!n.textContent.trim()) continue;
    const r = doc.createRange(); r.selectNodeContents(n);
    for (const q of r.getClientRects()) { min = Math.min(min, q.left); max = Math.max(max, q.right); }
  }
  return max > min ? max - min : 0;
}
function fit() {
  doc.querySelectorAll('[data-testid="stChatMessage"]').forEach(msg => {
    const md = msg.querySelector('[data-testid="stChatMessageContent"] [data-testid="stMarkdownContainer"]');
    if (!md) return;
    md.style.width = '';                       // always start from the natural width
    if (!msg.querySelector('[data-testid="stChatMessageAvatarUser"]')) return;  // assistant: stay full width
    const w = textWidth(md);
    if (!w) return;
    const cs = P.getComputedStyle(md);
    md.style.width = Math.ceil(w + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + 2) + 'px';
  });
}
let queued = false;
function schedule() { if (queued) return; queued = true; P.setTimeout(() => { queued = false; P.requestAnimationFrame(fit); }, 150); }
if (P.__bubbleObs) P.__bubbleObs.disconnect();
P.__bubbleObs = new MutationObserver(schedule);
P.__bubbleObs.observe(doc.body, { childList: true, subtree: true });
P.removeEventListener('resize', P.__bubbleResize || (() => {}));
P.__bubbleResize = schedule; P.addEventListener('resize', schedule);
schedule();
</script>
"""
components.html(FIT_JS, height=0)

# New message
if prompt:
    is_new = cid is None
    if is_new:
        cid = create_conversation(_uid(), title=generate_title(prompt))
        st.session_state.conversation_id = cid

    with st.chat_message("user"):
        st.markdown(prompt)
    save_message(cid, "user", prompt)
    messages.append({"role": "user", "content": prompt})

    # what the model sees: stored text, minus the "[RETRIEVED: ...]" caption markers
    llm_messages = [
        {"role": m["role"], "content": split_marker(m["content"])[0]} for m in messages
    ]

    context, rag_debug = retrieve_context(client, llm_messages[:-1], prompt)
    logging.warning("RAG: query=%r sources=%d errors=%s",
                    rag_debug.get("query"), len(rag_debug.get("sources", [])),
                    rag_debug.get("errors"))

    system = SYSTEM_PROMPT + (RAG_INSTRUCTIONS + context if context else "")
    api_messages = [{"role": "system", "content": system}] + llm_messages[-CONTEXT_LIMIT:]

    label = None
    raw_parts = []                                          # the reply as streamed, for the database
    with st.chat_message("assistant", avatar=AVATAR):
        try:
            stream = client.chat.completions.create(
                model=MODEL, messages=api_messages, stream=True
            )
            pieces = (clean_text(c.choices[0].delta.content) for c in stream if c.choices)
            st.write_stream(_collect(pieces, raw_parts))

            label = rag_debug.get("label") or None
            if label:
                st.caption("📖 From the manual: " + label)
        except RateLimitError as e:
            logging.warning("Groq rate limit hit: %s", e)
            wait = ""
            try:
                secs = int(float(e.response.headers.get("retry-after", "")))
                wait = f" Try again in about {max(1, round(secs / 60))} min." if secs >= 60 \
                    else f" Try again in about {secs} sec."
            except Exception:
                pass
            limit_msg = "The token limit for this test session has been reached." + wait
            st.warning(limit_msg)
            save_message(cid, "assistant", "⚠️ " + limit_msg)
            st.stop()
        except Exception as e:
            logging.exception("Groq request failed")
            st.error(f"Sorry, something went wrong ({type(e).__name__}: {e})")
            save_message(cid, "assistant", "⚠️ Sorry, I couldn't generate a reply. Please try again.")
            st.stop()

    response_text = "".join(raw_parts)
    if label:
        safe_label = label.replace("[", "(").replace("]", ")")   # brackets would break the marker
        response_text += f"\n\n[RETRIEVED: {safe_label}]"
    msg_id = save_message(cid, "assistant", response_text)
    try:
        save_sources(msg_id, rag_debug.get("sources") or [])
    except Exception:
        logging.exception("Could not save message sources")   # never block the answer

    if is_new:
        st.rerun()  # refresh the sidebar so the new chat appears
