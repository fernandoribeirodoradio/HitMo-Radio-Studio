# HitMo Radio Automation

Desktop radio automation prototype for Windows, built with Python and PySide6.

## First prototype

- Add audio files or folders by drag-and-drop.
- Adding a folder imports supported audio files in randomized order.
- Playlist supports multi-selection and row reordering.
- Delete removes items from the playlist only; it never deletes original media.
- Right-click menu with playback and playlist actions.
- Basic audio playback controls.

## Run on Windows

1. Install Python 3.11 or newer.
2. Open a terminal in this folder.
3. Run:

```powershell
py -m pip install -r requirements.txt
py main.py
```

This is an early prototype, not a production-ready broadcast playout system. Playback compatibility, playlist persistence, scheduling, metadata and robust on-air safeguards are planned for subsequent milestones.
