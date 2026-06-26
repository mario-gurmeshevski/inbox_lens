# Roadmap

This document outlines planned features and improvements for Inbox Lens.

## Multi-Account Support

Connect and monitor multiple email accounts simultaneously.

- Add multiple IMAP accounts via the web setup page
- Per-account keyword configuration and priority rules
- Unified inbox view across all accounts with account badges
- Individual account stats on the dashboard
- Isolated or shared SQLite storage per account

## Multi-Provider Support

Expand beyond Gmail to support other major email providers out of the box.

- **Outlook / Hotmail** (`outlook.office365.com`)
- **Yahoo Mail** (`imap.mail.yahoo.com`)
- **Apple Mail / iCloud** (`imap.mail.me.com`)
- Pre-configured IMAP server presets (select provider during setup instead of manual entry)

## Dark Mode

System-aware dark/light theme toggle for the web dashboard.

- Auto-detect system preference via `prefers-color-scheme`
- Manual toggle persisted across sessions
- Full CSS variable theme system (already partially in place)
- Optimized contrast for email body text and priority colors

## Email Actions

Manage emails directly from the dashboard without leaving the UI.

- Mark as read / unread
- Star / flag important emails
- Archive or move to folders
- Reply or forward via IMAP/SMTP
- Bulk actions on filtered email lists
