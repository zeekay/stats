# Core analyzer classes for GitHub Stats SDK
# Import from main app.py for now - will be refactored later

import os
import sqlite3
import requests
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from collections import defaultdict

# Default configuration
DEFAULT_CONFIG = {
    'github_token': os.getenv('GITHUB_TOKEN', ''),
    'github_users': [u.strip() for u in os.getenv('GITHUB_USERS', 'zeekay').split(',')],
    'db_path': Path(os.getenv('DB_PATH', './cache/stats.db')),
    'start_date': datetime.strptime(os.getenv('START_DATE', '2021-01-01'), '%Y-%m-%d').date(),
    'request_delay': float(os.getenv('REQUEST_DELAY', '0.1')),
}


class StatsDB:
    """SQLite database for GitHub stats."""

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DEFAULT_CONFIG['db_path']
        self.db_path.parent.mkdir(exist_ok=True)
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY, avatar_url TEXT, name TEXT, bio TEXT,
                company TEXT, location TEXT, blog TEXT, followers INTEGER DEFAULT 0,
                following INTEGER DEFAULT 0, public_repos INTEGER DEFAULT 0,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS commits (
                sha TEXT PRIMARY KEY, username TEXT NOT NULL, date TEXT NOT NULL,
                repo TEXT, message TEXT, url TEXT, additions INTEGER, deletions INTEGER,
                fetched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS fetch_meta (
                username TEXT, year_month TEXT, fetched_at TEXT,
                PRIMARY KEY (username, year_month)
            );
            CREATE INDEX IF NOT EXISTS idx_commits_user_date ON commits(username, date);
            CREATE INDEX IF NOT EXISTS idx_commits_repo ON commits(repo);
        ''')
        conn.commit()
        conn.close()

    def save_user(self, user_data: dict):
        conn = self._get_conn()
        conn.execute('''INSERT OR REPLACE INTO users
            (username, avatar_url, name, bio, company, location, blog,
             followers, following, public_repos, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_data.get('login'), user_data.get('avatar_url'), user_data.get('name'),
             user_data.get('bio'), user_data.get('company'), user_data.get('location'),
             user_data.get('blog'), user_data.get('followers', 0), user_data.get('following', 0),
             user_data.get('public_repos', 0), user_data.get('created_at'),
             datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_user(self, username: str) -> dict:
        conn = self._get_conn()
        row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def save_commits(self, commits: list) -> int:
        if not commits:
            return 0
        conn = self._get_conn()
        saved = 0
        for c in commits:
            try:
                conn.execute('''INSERT OR IGNORE INTO commits VALUES (?,?,?,?,?,?,?,?,?)''',
                    (c.get('sha'), c.get('username'), c.get('date'), c.get('repo'),
                     c.get('message'), c.get('url'), c.get('additions'), c.get('deletions'),
                     datetime.now().isoformat()))
                saved += conn.total_changes
            except:
                pass
        conn.commit()
        conn.close()
        return saved

    def get_stats(self, username: str) -> dict:
        """Get comprehensive stats for a user."""
        conn = self._get_conn()
        total = conn.execute('SELECT COUNT(*) FROM commits WHERE username = ?', (username,)).fetchone()[0]
        with_loc = conn.execute('SELECT COUNT(*) FROM commits WHERE username = ? AND additions IS NOT NULL', (username,)).fetchone()[0]
        totals = conn.execute('SELECT COALESCE(SUM(additions), 0), COALESCE(SUM(deletions), 0) FROM commits WHERE username = ?', (username,)).fetchone()
        dates = conn.execute('SELECT MIN(date), MAX(date) FROM commits WHERE username = ?', (username,)).fetchone()
        active_days = conn.execute('SELECT COUNT(DISTINCT date) FROM commits WHERE username = ?', (username,)).fetchone()[0]
        unique_repos = conn.execute('SELECT COUNT(DISTINCT repo) FROM commits WHERE username = ?', (username,)).fetchone()[0]
        conn.close()

        first_date = datetime.strptime(dates[0], '%Y-%m-%d') if dates[0] else datetime.now()
        years_coding = (datetime.now() - first_date).days / 365.25 if dates[0] else 0

        return {
            'total_commits': total, 'with_loc': with_loc, 'missing_loc': total - with_loc,
            'total_additions': totals[0], 'total_deletions': totals[1],
            'net_loc_change': totals[0] - totals[1],
            'first_commit': dates[0], 'last_commit': dates[1],
            'active_days': active_days, 'unique_repos': unique_repos,
            'years_coding': round(years_coding, 1),
            'average_commits': round(total / max(active_days, 1), 1),
        }


class GitHubAPI:
    """GitHub API client."""

    headers = {
        'Authorization': f'token {DEFAULT_CONFIG["github_token"]}',
        'Accept': 'application/vnd.github.v3+json'
    }

    @classmethod
    def get_user_profile(cls, username: str) -> dict:
        try:
            resp = requests.get(f'https://api.github.com/users/{username}',
                              headers=cls.headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
        return None

    @classmethod
    def search_commits(cls, username: str, start_date, end_date) -> tuple:
        commits = []
        page = 1
        query = f'author:{username} committer-date:{start_date}..{end_date}'
        search_headers = {**cls.headers, 'Accept': 'application/vnd.github.cloak-preview'}

        while page <= 10:
            try:
                resp = requests.get('https://api.github.com/search/commits',
                    headers=search_headers,
                    params={'q': query, 'per_page': 100, 'page': page, 'sort': 'committer-date'},
                    timeout=30)
                if resp.status_code != 200:
                    break
                data = resp.json()
                items = data.get('items', [])
                if not items:
                    break
                for item in items:
                    commit_obj = item.get('commit', {})
                    committer = commit_obj.get('committer', {})
                    commits.append({
                        'sha': item.get('sha', ''),
                        'username': username,
                        'date': committer.get('date', '')[:10],
                        'repo': item.get('repository', {}).get('full_name', ''),
                        'message': commit_obj.get('message', '').split('\n')[0][:200],
                        'url': item.get('url', ''),
                    })
                if len(items) < 100:
                    break
                page += 1
                time.sleep(DEFAULT_CONFIG['request_delay'] * 2)
            except:
                break
        return commits, len(commits) >= 1000


class GitHubStatsAnalyzer:
    """Main analyzer class."""

    def __init__(self, db_path: Path = None):
        self.db = StatsDB(db_path)

    def fetch_user_profile(self, username: str):
        profile = GitHubAPI.get_user_profile(username)
        if profile:
            self.db.save_user(profile)
        return profile

    def get_user_data(self, username: str, fetch: bool = False) -> dict:
        if fetch:
            self.fetch_all_commits(username)

        user = self.db.get_user(username)
        stats = self.db.get_stats(username)

        return {'user': user, 'stats': stats}
