#!/usr/bin/env python3
"""
GitHub Stats - Analyze your real contribution history.

Features:
- SQLite-backed storage with rich query support
- GitHub user avatars and profile data
- Top repos by commits, lines added/deleted
- Interactive exploration with filtering
- Static export for GitHub Pages
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, g
import requests
import pandas as pd
import numpy as np
import plotly.graph_objs as go
import plotly.express as px
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import time

load_dotenv()

app = Flask(__name__)

# Load config from stats.json if exists, fallback to env vars
CONFIG_PATH = Path('stats.json')
config = {}
if CONFIG_PATH.exists():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

# Configuration (stats.json takes precedence over env vars)
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
GITHUB_USERS = config.get('users') or [user.strip() for user in os.getenv('GITHUB_USERS', 'zeekay').split(',')]
DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'
PORT = config.get('port') or int(os.getenv('PORT', '5001'))
START_DATE = datetime.strptime(config.get('since') or os.getenv('START_DATE', '2021-01-01'), '%Y-%m-%d').date()
DB_PATH = Path(config.get('database') or os.getenv('DB_PATH', './cache/stats.db'))
REQUEST_DELAY = float(os.getenv('REQUEST_DELAY', '0.1'))
APP_TITLE = config.get('title', 'GitHub Stats')

# API Headers
HEADERS = {'Authorization': f'bearer {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
REST_HEADERS = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
SEARCH_HEADERS = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.cloak-preview'}

# Ensure cache directory exists
DB_PATH.parent.mkdir(exist_ok=True)


class StatsDB:
    """SQLite database for GitHub stats with rich query support."""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _get_conn(self):
        """Get thread-local connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_conn()
        conn.executescript('''
            -- Users table with profile info
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                avatar_url TEXT,
                name TEXT,
                bio TEXT,
                company TEXT,
                location TEXT,
                blog TEXT,
                followers INTEGER DEFAULT 0,
                following INTEGER DEFAULT 0,
                public_repos INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );
            
            -- Commits table
            CREATE TABLE IF NOT EXISTS commits (
                sha TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                date TEXT NOT NULL,
                repo TEXT,
                message TEXT,
                url TEXT,
                additions INTEGER,
                deletions INTEGER,
                fetched_at TEXT,
                FOREIGN KEY (username) REFERENCES users(username)
            );
            
            -- Repos summary table (aggregated)
            CREATE TABLE IF NOT EXISTS repos (
                repo TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                commit_count INTEGER DEFAULT 0,
                additions INTEGER DEFAULT 0,
                deletions INTEGER DEFAULT 0,
                first_commit TEXT,
                last_commit TEXT,
                FOREIGN KEY (username) REFERENCES users(username)
            );
            
            -- Fetch metadata
            CREATE TABLE IF NOT EXISTS fetch_meta (
                username TEXT,
                year_month TEXT,
                fetched_at TEXT,
                PRIMARY KEY (username, year_month)
            );

            -- Languages per repo
            CREATE TABLE IF NOT EXISTS languages (
                repo TEXT NOT NULL,
                username TEXT NOT NULL,
                language TEXT NOT NULL,
                bytes INTEGER DEFAULT 0,
                fetched_at TEXT,
                PRIMARY KEY (repo, language)
            );

            -- Topics/tags per repo
            CREATE TABLE IF NOT EXISTS topics (
                repo TEXT NOT NULL,
                username TEXT NOT NULL,
                topic TEXT NOT NULL,
                fetched_at TEXT,
                PRIMARY KEY (repo, topic)
            );

            -- Indexes for fast queries
            CREATE INDEX IF NOT EXISTS idx_commits_user_date ON commits(username, date);
            CREATE INDEX IF NOT EXISTS idx_commits_repo ON commits(repo);
            CREATE INDEX IF NOT EXISTS idx_commits_date ON commits(date);
            CREATE INDEX IF NOT EXISTS idx_repos_username ON repos(username);
        ''')
        conn.commit()
        conn.close()
    
    def save_user(self, user_data: dict):
        """Save user profile data."""
        conn = self._get_conn()
        conn.execute('''
            INSERT OR REPLACE INTO users 
            (username, avatar_url, name, bio, company, location, blog, 
             followers, following, public_repos, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_data.get('login'),
            user_data.get('avatar_url'),
            user_data.get('name'),
            user_data.get('bio'),
            user_data.get('company'),
            user_data.get('location'),
            user_data.get('blog'),
            user_data.get('followers', 0),
            user_data.get('following', 0),
            user_data.get('public_repos', 0),
            user_data.get('created_at'),
            datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()
    
    def get_user(self, username: str) -> dict:
        """Get user profile data."""
        conn = self._get_conn()
        row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        return dict(row) if row else None
    
    def save_commits(self, commits: list):
        """Save commits in batch."""
        if not commits:
            return 0
        
        conn = self._get_conn()
        saved = 0
        for commit in commits:
            try:
                conn.execute('''
                    INSERT OR IGNORE INTO commits 
                    (sha, username, date, repo, message, url, additions, deletions, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    commit.get('sha'),
                    commit.get('username'),
                    commit.get('date'),
                    commit.get('repo'),
                    commit.get('message'),
                    commit.get('url'),
                    commit.get('additions'),
                    commit.get('deletions'),
                    datetime.now().isoformat()
                ))
                saved += conn.total_changes
            except:
                pass
        conn.commit()
        conn.close()
        return saved
    
    def update_commit_loc(self, sha: str, additions: int, deletions: int):
        """Update LOC data for a commit."""
        conn = self._get_conn()
        conn.execute('''
            UPDATE commits SET additions = ?, deletions = ? WHERE sha = ?
        ''', (additions, deletions, sha))
        conn.commit()
        conn.close()
    
    def get_commits_needing_loc(self, username: str, limit: int = 500) -> list:
        """Get commits that need LOC data."""
        conn = self._get_conn()
        rows = conn.execute('''
            SELECT sha, url FROM commits 
            WHERE username = ? AND additions IS NULL 
            LIMIT ?
        ''', (username, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def mark_month_fetched(self, username: str, year: int, month: int):
        """Mark a month as fetched."""
        conn = self._get_conn()
        conn.execute('''
            INSERT OR REPLACE INTO fetch_meta (username, year_month, fetched_at)
            VALUES (?, ?, ?)
        ''', (username, f'{year}-{month:02d}', datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def is_month_fetched(self, username: str, year: int, month: int) -> bool:
        """Check if month is fetched."""
        conn = self._get_conn()
        row = conn.execute('''
            SELECT 1 FROM fetch_meta WHERE username = ? AND year_month = ?
        ''', (username, f'{year}-{month:02d}')).fetchone()
        conn.close()
        return row is not None
    
    def get_stats(self, username: str) -> dict:
        """Get comprehensive stats for a user - the god-tier metrics."""
        conn = self._get_conn()

        # Basic counts
        total = conn.execute('SELECT COUNT(*) FROM commits WHERE username = ?', (username,)).fetchone()[0]
        with_loc = conn.execute('SELECT COUNT(*) FROM commits WHERE username = ? AND additions IS NOT NULL', (username,)).fetchone()[0]

        # Totals
        totals = conn.execute('''
            SELECT
                COALESCE(SUM(additions), 0) as total_additions,
                COALESCE(SUM(deletions), 0) as total_deletions
            FROM commits WHERE username = ?
        ''', (username,)).fetchone()

        # Date range
        dates = conn.execute('''
            SELECT MIN(date) as first, MAX(date) as last
            FROM commits WHERE username = ?
        ''', (username,)).fetchone()

        # Active days (unique days with commits)
        active_days = conn.execute('''
            SELECT COUNT(DISTINCT date) FROM commits WHERE username = ?
        ''', (username,)).fetchone()[0]

        # Unique repos
        unique_repos = conn.execute('''
            SELECT COUNT(DISTINCT repo) FROM commits WHERE username = ?
        ''', (username,)).fetchone()[0]

        # Max commits in a day
        max_day = conn.execute('''
            SELECT date, COUNT(*) as cnt FROM commits
            WHERE username = ? GROUP BY date ORDER BY cnt DESC LIMIT 1
        ''', (username,)).fetchone()

        # Day of week analysis
        dow_stats = conn.execute('''
            SELECT
                CASE CAST(strftime('%w', date) AS INTEGER)
                    WHEN 0 THEN 'Sunday'
                    WHEN 1 THEN 'Monday'
                    WHEN 2 THEN 'Tuesday'
                    WHEN 3 THEN 'Wednesday'
                    WHEN 4 THEN 'Thursday'
                    WHEN 5 THEN 'Friday'
                    WHEN 6 THEN 'Saturday'
                END as day,
                COUNT(*) as commits
            FROM commits WHERE username = ?
            GROUP BY strftime('%w', date) ORDER BY commits DESC
        ''', (username,)).fetchall()

        # Yearly breakdown
        yearly = {}
        yearly_rows = conn.execute('''
            SELECT
                strftime('%Y', date) as year,
                COUNT(*) as commits,
                COALESCE(SUM(additions), 0) as additions,
                COALESCE(SUM(deletions), 0) as deletions,
                COUNT(DISTINCT date) as days_active
            FROM commits WHERE username = ?
            GROUP BY strftime('%Y', date) ORDER BY year
        ''', (username,)).fetchall()
        for row in yearly_rows:
            yearly[row['year']] = {
                'commits': row['commits'],
                'additions': row['additions'],
                'deletions': row['deletions'],
                'net_loc': row['additions'] - row['deletions'],
                'days_active': row['days_active']
            }

        # Streak calculation
        all_dates = [r[0] for r in conn.execute(
            'SELECT DISTINCT date FROM commits WHERE username = ? ORDER BY date', (username,)
        ).fetchall()]

        current_streak = 0
        longest_streak = 0
        if all_dates:
            today = datetime.now().strftime('%Y-%m-%d')
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

            # Calculate current streak
            if all_dates and (all_dates[-1] == today or all_dates[-1] == yesterday):
                current_streak = 1
                for i in range(len(all_dates) - 2, -1, -1):
                    d1 = datetime.strptime(all_dates[i], '%Y-%m-%d')
                    d2 = datetime.strptime(all_dates[i + 1], '%Y-%m-%d')
                    if (d2 - d1).days == 1:
                        current_streak += 1
                    else:
                        break

            # Calculate longest streak
            if all_dates:
                streak = 1
                for i in range(1, len(all_dates)):
                    d1 = datetime.strptime(all_dates[i - 1], '%Y-%m-%d')
                    d2 = datetime.strptime(all_dates[i], '%Y-%m-%d')
                    if (d2 - d1).days == 1:
                        streak += 1
                    else:
                        longest_streak = max(longest_streak, streak)
                        streak = 1
                longest_streak = max(longest_streak, streak)

        # Average commit size
        avg_loc = conn.execute('''
            SELECT AVG(additions + deletions) FROM commits
            WHERE username = ? AND additions IS NOT NULL
        ''', (username,)).fetchone()[0] or 0

        # Time period metrics
        today = datetime.now()
        year_start = datetime(today.year, 1, 1).strftime('%Y-%m-%d')

        periods = {
            '7d': (today - timedelta(days=7)).strftime('%Y-%m-%d'),
            '30d': (today - timedelta(days=30)).strftime('%Y-%m-%d'),
            '90d': (today - timedelta(days=90)).strftime('%Y-%m-%d'),
            'ytd': year_start,
            '1y': (today - timedelta(days=365)).strftime('%Y-%m-%d'),
        }

        period_stats = {}
        for period, start_date in periods.items():
            stats = conn.execute('''
                SELECT
                    COUNT(*) as commits,
                    COALESCE(SUM(additions), 0) as additions,
                    COALESCE(SUM(deletions), 0) as deletions,
                    COUNT(DISTINCT date) as active_days,
                    COUNT(DISTINCT repo) as repos
                FROM commits WHERE username = ? AND date >= ?
            ''', (username, start_date)).fetchone()
            period_stats[period] = dict(stats)

        # 30-day comparison for change calculation
        thirty_days_ago = periods['30d']
        sixty_days_ago = (today - timedelta(days=60)).strftime('%Y-%m-%d')

        prev_30d = conn.execute('''
            SELECT COALESCE(SUM(additions), 0) as adds, COALESCE(SUM(deletions), 0) as dels
            FROM commits WHERE username = ? AND date >= ? AND date < ?
        ''', (username, sixty_days_ago, thirty_days_ago)).fetchone()

        # Calculate percentage change
        additions_30d_change = 0
        deletions_30d_change = 0
        last_30d_adds = period_stats['30d']['additions']
        last_30d_dels = period_stats['30d']['deletions']
        if prev_30d['adds'] > 0:
            additions_30d_change = round(((last_30d_adds - prev_30d['adds']) / prev_30d['adds']) * 100, 0)
        elif last_30d_adds > 0:
            additions_30d_change = 100
        if prev_30d['dels'] > 0:
            deletions_30d_change = round(((last_30d_dels - prev_30d['dels']) / prev_30d['dels']) * 100, 0)
        elif last_30d_dels > 0:
            deletions_30d_change = 100

        conn.close()

        # Calculate derived stats
        first_date = datetime.strptime(dates['first'], '%Y-%m-%d') if dates['first'] else datetime.now()
        last_date = datetime.strptime(dates['last'], '%Y-%m-%d') if dates['last'] else datetime.now()
        total_days = (last_date - first_date).days + 1 if dates['first'] else 0
        years_coding = (datetime.now() - first_date).days / 365.25 if dates['first'] else 0

        return {
            'total_commits': total,
            'with_loc': with_loc,
            'missing_loc': total - with_loc,
            'total_additions': totals['total_additions'],
            'total_deletions': totals['total_deletions'],
            'net_loc_change': totals['total_additions'] - totals['total_deletions'],
            'first_commit': dates['first'],
            'last_commit': dates['last'],
            'total_days': total_days,
            'active_days': active_days,
            'unique_repos': unique_repos,
            'years_coding': round(years_coding, 1),
            'average_commits': round(total / max(active_days, 1), 1),
            'average_loc_per_commit': round(avg_loc, 0),
            'maximum_commits': max_day['cnt'] if max_day else 0,
            'max_commit_date': max_day['date'] if max_day else None,
            'current_streak': current_streak,
            'longest_streak': longest_streak,
            'most_productive_day': dow_stats[0]['day'] if dow_stats else None,
            'day_of_week_stats': {r['day']: r['commits'] for r in dow_stats},
            'yearly': yearly,
            # Time period metrics
            'periods': period_stats,
            'additions_30d_change': additions_30d_change,
            'deletions_30d_change': deletions_30d_change,
        }
    
    def get_daily_stats(self, username: str, since: str = None) -> list:
        """Get daily commit stats."""
        conn = self._get_conn()
        query = '''
            SELECT 
                date,
                COUNT(*) as commits,
                COALESCE(SUM(additions), 0) as additions,
                COALESCE(SUM(deletions), 0) as deletions,
                COUNT(DISTINCT repo) as repos
            FROM commits 
            WHERE username = ?
        '''
        params = [username]
        if since:
            query += ' AND date >= ?'
            params.append(since)
        query += ' GROUP BY date ORDER BY date'
        
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def get_yearly_stats(self, username: str) -> list:
        """Get yearly aggregated stats."""
        conn = self._get_conn()
        rows = conn.execute('''
            SELECT 
                strftime('%Y', date) as year,
                COUNT(*) as commits,
                COALESCE(SUM(additions), 0) as additions,
                COALESCE(SUM(deletions), 0) as deletions,
                COUNT(DISTINCT repo) as repos,
                COUNT(DISTINCT date) as days_active
            FROM commits 
            WHERE username = ?
            GROUP BY year
            ORDER BY year DESC
        ''', (username,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def get_top_repos(self, username: str, limit: int = 20, order_by: str = 'commits') -> list:
        """Get top repositories by commits or LOC."""
        conn = self._get_conn()
        order_col = 'commits' if order_by == 'commits' else 'additions'
        rows = conn.execute(f'''
            SELECT 
                repo,
                COUNT(*) as commits,
                COALESCE(SUM(additions), 0) as additions,
                COALESCE(SUM(deletions), 0) as deletions,
                MIN(date) as first_commit,
                MAX(date) as last_commit
            FROM commits 
            WHERE username = ? AND repo IS NOT NULL AND repo != ''
            GROUP BY repo
            ORDER BY {order_col} DESC
            LIMIT ?
        ''', (username, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def get_monthly_stats(self, username: str) -> list:
        """Get monthly aggregated stats for heatmap."""
        conn = self._get_conn()
        rows = conn.execute('''
            SELECT 
                strftime('%Y', date) as year,
                strftime('%m', date) as month,
                COUNT(*) as commits,
                COALESCE(SUM(additions), 0) as additions,
                COALESCE(SUM(deletions), 0) as deletions
            FROM commits 
            WHERE username = ?
            GROUP BY year, month
            ORDER BY year, month
        ''', (username,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def get_recent_commits(self, username: str, limit: int = 50) -> list:
        """Get recent commits."""
        conn = self._get_conn()
        rows = conn.execute('''
            SELECT sha, date, repo, message, additions, deletions
            FROM commits 
            WHERE username = ?
            ORDER BY date DESC
            LIMIT ?
        ''', (username, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def search_commits(self, username: str, query: str, limit: int = 100) -> list:
        """Search commits by message or repo."""
        conn = self._get_conn()
        rows = conn.execute('''
            SELECT sha, date, repo, message, additions, deletions
            FROM commits 
            WHERE username = ? AND (message LIKE ? OR repo LIKE ?)
            ORDER BY date DESC
            LIMIT ?
        ''', (username, f'%{query}%', f'%{query}%', limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]


class GitHubAPI:
    """GitHub API client."""
    
    @staticmethod
    def get_user_profile(username: str) -> dict:
        """Fetch user profile from GitHub."""
        try:
            resp = requests.get(f'https://api.github.com/users/{username}', 
                              headers=REST_HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
        return None
    
    @staticmethod
    def search_commits(username: str, start_date, end_date) -> tuple:
        """Search for commits by author in a date range."""
        commits = []
        page = 1
        query = f'author:{username} committer-date:{start_date}..{end_date}'
        
        while page <= 10:
            url = 'https://api.github.com/search/commits'
            params = {'q': query, 'per_page': 100, 'page': page, 'sort': 'committer-date'}
            
            try:
                resp = requests.get(url, headers=SEARCH_HEADERS, params=params, timeout=30)
                
                if resp.status_code == 403:
                    reset_time = int(resp.headers.get('X-RateLimit-Reset', 0))
                    wait_time = max(reset_time - time.time(), 60)
                    print(f'    Rate limited, waiting {wait_time:.0f}s...')
                    time.sleep(wait_time)
                    continue
                
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
                        'additions': None,
                        'deletions': None
                    })
                
                if len(items) < 100:
                    break
                
                page += 1
                time.sleep(REQUEST_DELAY * 2)
            
            except Exception as e:
                print(f'    Search error: {e}')
                break
        
        return commits, len(commits) >= 1000
    
    @staticmethod
    def get_commit_stats(url: str) -> tuple:
        """Fetch LOC stats for a commit."""
        try:
            resp = requests.get(url, headers=REST_HEADERS, timeout=30)
            if resp.status_code == 200:
                stats = resp.json().get('stats', {})
                return stats.get('additions', 0), stats.get('deletions', 0)
        except:
            pass
        return None, None


class GitHubStatsAnalyzer:
    """Main analyzer class."""
    
    def __init__(self):
        self.db = StatsDB()
    
    def fetch_user_profile(self, username: str):
        """Fetch and cache user profile."""
        profile = GitHubAPI.get_user_profile(username)
        if profile:
            self.db.save_user(profile)
            print(f'  Fetched profile for {username}')
        return profile
    
    def fetch_commits_for_month(self, username: str, year: int, month: int) -> list:
        """Fetch commits for a specific month."""
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        
        commits, hit_limit = GitHubAPI.search_commits(username, start_date, end_date)
        
        if hit_limit:
            print(f'    Hit 1000 limit, splitting into weeks...')
            commits = []
            week_start = start_date
            
            while week_start <= end_date:
                week_end = min(week_start + timedelta(days=6), end_date)
                week_commits, week_hit = GitHubAPI.search_commits(username, week_start, week_end)
                
                if week_hit:
                    print(f'    Week {week_start} hit limit, splitting into days...')
                    for day_offset in range(7):
                        day = week_start + timedelta(days=day_offset)
                        if day > end_date:
                            break
                        day_commits, _ = GitHubAPI.search_commits(username, day, day)
                        commits.extend(day_commits)
                        time.sleep(REQUEST_DELAY)
                else:
                    commits.extend(week_commits)
                
                week_start = week_end + timedelta(days=1)
                time.sleep(REQUEST_DELAY)
        
        return commits
    
    def fetch_all_commits(self, username: str, since_date=None):
        """Fetch all commits for a user."""
        if since_date is None:
            since_date = START_DATE
        
        # Fetch profile first
        if not self.db.get_user(username):
            self.fetch_user_profile(username)
        
        end_date = datetime.now().date()
        current = since_date
        months_to_fetch = []
        
        while current <= end_date:
            year, month = current.year, current.month
            if not self.db.is_month_fetched(username, year, month) or \
               (year == end_date.year and month == end_date.month):
                months_to_fetch.append((year, month))
            
            if month == 12:
                current = date(year + 1, 1, 1)
            else:
                current = date(year, month + 1, 1)
        
        print(f'  {len(months_to_fetch)} months to fetch')
        
        for year, month in months_to_fetch:
            print(f'  Fetching {year}-{month:02d}...')
            commits = self.fetch_commits_for_month(username, year, month)
            print(f'    Found {len(commits)} commits')
            
            saved = self.db.save_commits(commits)
            print(f'    Saved {saved} new commits')
            
            self.db.mark_month_fetched(username, year, month)
            time.sleep(REQUEST_DELAY)
    
    def fetch_loc_batch(self, username: str, batch_size: int = 500) -> int:
        """Fetch LOC data for commits that don't have it."""
        commits = self.db.get_commits_needing_loc(username, batch_size)
        print(f'  {len(commits)} commits need LOC data')
        
        fetched = 0
        for commit in commits:
            additions, deletions = GitHubAPI.get_commit_stats(commit['url'])
            if additions is not None:
                self.db.update_commit_loc(commit['sha'], additions, deletions)
                fetched += 1
                
                if fetched % 50 == 0:
                    print(f'    Fetched {fetched}/{len(commits)} LOC stats')
            
            time.sleep(REQUEST_DELAY)
        
        return fetched

    def fetch_languages(self, username: str) -> int:
        """Fetch language stats for all repos of a user."""
        # Get all unique repos for this user
        conn = self.db._get_conn()
        repos = conn.execute('''
            SELECT DISTINCT repo FROM commits
            WHERE username = ? AND repo IS NOT NULL AND repo != ''
        ''', (username,)).fetchall()
        conn.close()

        fetched = 0
        for (repo,) in repos:
            # Check if already fetched
            conn = self.db._get_conn()
            existing = conn.execute('SELECT 1 FROM languages WHERE repo = ?', (repo,)).fetchone()
            conn.close()
            if existing:
                continue

            # Fetch from GitHub
            try:
                resp = requests.get(
                    f'https://api.github.com/repos/{repo}/languages',
                    headers=GitHubAPI.get_headers()
                )
                if resp.status_code == 200:
                    languages = resp.json()
                    conn = self.db._get_conn()
                    for lang, bytes_count in languages.items():
                        conn.execute('''
                            INSERT OR REPLACE INTO languages (repo, username, language, bytes, fetched_at)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (repo, username, lang, bytes_count, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    fetched += 1

                    if fetched % 20 == 0:
                        print(f'    Fetched languages for {fetched} repos')

                time.sleep(REQUEST_DELAY)
            except Exception as e:
                print(f'    Error fetching languages for {repo}: {e}')

        return fetched

    def fetch_topics(self, username: str, limit: int = None) -> int:
        """Fetch topics/tags for repos of a user."""
        conn = self.db._get_conn()
        # Get repos ordered by commit count, optionally limited
        query = '''
            SELECT repo, COUNT(*) as commits FROM commits
            WHERE username = ? AND repo IS NOT NULL AND repo != ''
            GROUP BY repo ORDER BY commits DESC
        '''
        if limit:
            query += f' LIMIT {limit}'
        repos = conn.execute(query, (username,)).fetchall()
        conn.close()

        fetched = 0
        for row in repos:
            repo = row[0]
            # Check if already fetched
            conn = self.db._get_conn()
            existing = conn.execute('SELECT 1 FROM topics WHERE repo = ?', (repo,)).fetchone()
            conn.close()
            if existing:
                continue

            # Fetch from GitHub (need to accept topics preview header)
            try:
                resp = requests.get(
                    f'https://api.github.com/repos/{repo}',
                    headers={**REST_HEADERS, 'Accept': 'application/vnd.github.mercy-preview+json'}
                )
                if resp.status_code == 200:
                    repo_data = resp.json()
                    topics = repo_data.get('topics', [])
                    if topics:
                        conn = self.db._get_conn()
                        for topic in topics:
                            conn.execute('''
                                INSERT OR REPLACE INTO topics (repo, username, topic, fetched_at)
                                VALUES (?, ?, ?, ?)
                            ''', (repo, username, topic, datetime.now().isoformat()))
                        conn.commit()
                        conn.close()
                    fetched += 1

                    if fetched % 20 == 0:
                        print(f'    Fetched topics for {fetched} repos')

                time.sleep(REQUEST_DELAY)
            except Exception as e:
                print(f'    Error fetching topics for {repo}: {e}')

        return fetched

    def get_user_data(self, username: str, fetch: bool = False) -> dict:
        """Get all data for a user. If fetch=True, fetches new data from GitHub first."""
        if fetch:
            self.fetch_all_commits(username)
            self.fetch_loc_batch(username, 200)

        user = self.db.get_user(username)
        stats = self.db.get_stats(username)
        daily = self.db.get_daily_stats(username, START_DATE.isoformat())
        yearly = self.db.get_yearly_stats(username)
        top_repos = self.db.get_top_repos(username, 15, 'commits')
        top_repos_loc = self.db.get_top_repos(username, 15, 'additions')
        monthly = self.db.get_monthly_stats(username)
        recent = self.db.get_recent_commits(username, 20)

        return {
            'user': user,
            'stats': stats,
            'daily': daily,
            'yearly': yearly,
            'top_repos': top_repos,
            'top_repos_loc': top_repos_loc,
            'monthly': monthly,
            'recent': recent
        }


def create_visualizations(data: dict) -> dict:
    """Create Plotly visualizations."""
    viz = {}
    
    # Daily commits line chart
    today = pd.Timestamp.now().normalize()
    if data.get('daily'):
        df = pd.DataFrame(data['daily'])
        df['date'] = pd.to_datetime(df['date'])
        df = df[df['date'] <= today]  # Filter to today
        df['commits_7d'] = df['commits'].rolling(7, min_periods=1).mean()
        df['commits_30d'] = df['commits'].rolling(30, min_periods=1).mean()

        min_date = df['date'].min() if len(df) > 0 else today
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits'], mode='lines',
                                name='Daily', line=dict(color='rgba(255,255,255,0.2)', width=1)))
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits_7d'], mode='lines',
                                name='7-day avg', line=dict(color='#fff', width=2)))
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits_30d'], mode='lines',
                                name='30-day avg', line=dict(color='rgba(255,255,255,0.5)', width=2, dash='dash')))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#fff'), margin=dict(l=40, r=20, t=20, b=40),
            xaxis=dict(gridcolor='#262626', range=[min_date, today]),
            yaxis=dict(gridcolor='#262626'),
            legend=dict(orientation='h', y=1.1)
        )
        viz['commits_timeline'] = fig.to_json()
    
    # LOC area chart
    if data.get('daily'):
        df = pd.DataFrame(data['daily'])
        df['date'] = pd.to_datetime(df['date'])
        # Filter to only dates up to today
        today = pd.Timestamp.now().normalize()
        df = df[df['date'] <= today]
        df['net'] = df['additions'] - df['deletions']
        df['net_30d'] = df['net'].rolling(30, min_periods=1).mean()

        fig = go.Figure()
        fig.add_trace(go.Bar(x=df['date'], y=df['additions'], name='Added',
                            marker_color='rgba(255,255,255,0.7)'))
        fig.add_trace(go.Bar(x=df['date'], y=-df['deletions'], name='Deleted',
                            marker_color='rgba(255,255,255,0.3)'))
        fig.add_trace(go.Scatter(x=df['date'], y=df['net_30d'], mode='lines',
                                name='Net (30d)', line=dict(color='#fff', width=3)))
        # Set x-axis range to end at today
        min_date = df['date'].min() if len(df) > 0 else today
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#fff'), margin=dict(l=40, r=20, t=20, b=40), barmode='relative',
            xaxis=dict(gridcolor='#262626', range=[min_date, today]),
            yaxis=dict(gridcolor='#262626'),
            legend=dict(orientation='h', y=1.1)
        )
        viz['loc_timeline'] = fig.to_json()
    
    # Top repos bar chart
    if data.get('top_repos'):
        df = pd.DataFrame(data['top_repos'][:10])
        df['short_repo'] = df['repo'].apply(lambda x: x.split('/')[-1][:20] if x else '')
        
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df['commits'], y=df['short_repo'], orientation='h',
                            marker_color='#fff', text=df['commits'], textposition='auto'))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#fff'), margin=dict(l=120, r=20, t=20, b=40),
            yaxis=dict(autorange='reversed'), xaxis_title='Commits'
        )
        viz['top_repos'] = fig.to_json()
    
    # Top repos by LOC
    if data.get('top_repos_loc'):
        df = pd.DataFrame(data['top_repos_loc'][:10])
        df['short_repo'] = df['repo'].apply(lambda x: x.split('/')[-1][:20] if x else '')
        
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df['additions'], y=df['short_repo'], orientation='h', name='Added',
                            marker_color='rgba(255,255,255,0.8)'))
        fig.add_trace(go.Bar(x=-df['deletions'], y=df['short_repo'], orientation='h', name='Deleted',
                            marker_color='rgba(255,255,255,0.3)'))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#fff'), margin=dict(l=120, r=20, t=20, b=40), barmode='relative',
            yaxis=dict(autorange='reversed'), xaxis_title='Lines of Code',
            legend=dict(orientation='h', y=1.1)
        )
        viz['top_repos_loc'] = fig.to_json()
    
    # Monthly heatmap
    if data.get('monthly'):
        df = pd.DataFrame(data['monthly'])
        pivot = df.pivot(index='year', columns='month', values='commits').fillna(0)
        
        fig = go.Figure(data=go.Heatmap(
            z=pivot.values,
            x=['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
            y=[str(y) for y in pivot.index],
            colorscale=[[0, '#0a0a0a'], [0.5, '#404040'], [1, '#ffffff']],
            colorbar=dict(title='Commits')
        ))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#fff'), margin=dict(l=40, r=20, t=20, b=40)
        )
        viz['heatmap'] = fig.to_json()
    
    # Yearly bar chart
    if data.get('yearly'):
        df = pd.DataFrame(data['yearly'])

        fig = go.Figure()
        fig.add_trace(go.Bar(x=df['year'], y=df['commits'], name='Commits',
                            marker_color='#fff', text=df['commits'], textposition='auto'))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#fff'), margin=dict(l=40, r=20, t=20, b=40)
        )
        viz['yearly_commits'] = fig.to_json()

    # Day of week bar chart
    stats = data.get('stats', {})
    dow_stats = stats.get('day_of_week_stats', {})
    if dow_stats:
        days_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        days = [d for d in days_order if d in dow_stats]
        values = [dow_stats.get(d, 0) for d in days]

        fig = go.Figure()
        fig.add_trace(go.Bar(x=days, y=values, marker_color='#fff', text=values, textposition='auto'))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#fff'), margin=dict(l=40, r=20, t=20, b=40)
        )
        viz['day_of_week'] = fig.to_json()

    return viz


# Initialize analyzer
analyzer = GitHubStatsAnalyzer()


def init_default_users():
    """Fetch data for default users on startup if they don't have data."""
    for username in GITHUB_USERS:
        stats = analyzer.db.get_stats(username)
        if stats.get('total_commits', 0) == 0:
            print(f'\nInitializing data for {username}...')
            # Fetch user profile
            user_data = GitHubAPI.get_user_profile(username)
            if user_data:
                analyzer.db.save_user(user_data)
                print(f'  Saved profile for {user_data.get("name", username)}')
            # Fetch commits
            analyzer.fetch_all_commits(username)
            # Fetch some LOC data
            analyzer.fetch_loc_batch(username, 100)
        else:
            # Check if user profile exists
            user = analyzer.db.get_user(username)
            if not user:
                print(f'\nFetching profile for {username}...')
                user_data = GitHubAPI.get_user_profile(username)
                if user_data:
                    analyzer.db.save_user(user_data)
                    print(f'  Saved profile for {user_data.get("name", username)}')


# Run initialization in background thread on startup
import threading
init_thread = threading.Thread(target=init_default_users, daemon=True)
init_thread.start()


# Flask routes
@app.route('/')
def index():
    return render_template('index.html')


def get_combined_stats(usernames: list) -> dict:
    """Get combined stats across multiple users."""
    if not usernames:
        raise ValueError("usernames list cannot be empty")
    conn = analyzer.db._get_conn()

    # Combined totals
    placeholders = ','.join(['?' for _ in usernames])
    total = conn.execute(f'SELECT COUNT(*) FROM commits WHERE username IN ({placeholders})', usernames).fetchone()[0]
    with_loc = conn.execute(f'SELECT COUNT(*) FROM commits WHERE username IN ({placeholders}) AND additions IS NOT NULL', usernames).fetchone()[0]

    totals = conn.execute(f'''
        SELECT COALESCE(SUM(additions), 0) as total_additions, COALESCE(SUM(deletions), 0) as total_deletions
        FROM commits WHERE username IN ({placeholders})
    ''', usernames).fetchone()

    dates = conn.execute(f'SELECT MIN(date) as first, MAX(date) as last FROM commits WHERE username IN ({placeholders})', usernames).fetchone()
    active_days = conn.execute(f'SELECT COUNT(DISTINCT date) FROM commits WHERE username IN ({placeholders})', usernames).fetchone()[0]
    unique_repos = conn.execute(f'SELECT COUNT(DISTINCT repo) FROM commits WHERE username IN ({placeholders})', usernames).fetchone()[0]

    # Max commits in a day
    max_day = conn.execute(f'''
        SELECT date, COUNT(*) as cnt FROM commits
        WHERE username IN ({placeholders}) GROUP BY date ORDER BY cnt DESC LIMIT 1
    ''', usernames).fetchone()

    # Day of week analysis
    dow_stats = conn.execute(f'''
        SELECT
            CASE CAST(strftime('%w', date) AS INTEGER)
                WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday'
                WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday'
            END as day, COUNT(*) as commits
        FROM commits WHERE username IN ({placeholders})
        GROUP BY strftime('%w', date) ORDER BY commits DESC
    ''', usernames).fetchall()

    # Average LOC per commit
    avg_loc = conn.execute(f'''
        SELECT AVG(additions + deletions) FROM commits
        WHERE username IN ({placeholders}) AND additions IS NOT NULL
    ''', usernames).fetchone()[0] or 0

    # Streak calculation
    all_dates = [r[0] for r in conn.execute(
        f'SELECT DISTINCT date FROM commits WHERE username IN ({placeholders}) ORDER BY date', usernames
    ).fetchall()]

    current_streak = 0
    longest_streak = 0
    if all_dates:
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        if all_dates[-1] == today or all_dates[-1] == yesterday:
            current_streak = 1
            for i in range(len(all_dates) - 2, -1, -1):
                d1 = datetime.strptime(all_dates[i], '%Y-%m-%d')
                d2 = datetime.strptime(all_dates[i + 1], '%Y-%m-%d')
                if (d2 - d1).days == 1:
                    current_streak += 1
                else:
                    break

        streak = 1
        for i in range(1, len(all_dates)):
            d1 = datetime.strptime(all_dates[i - 1], '%Y-%m-%d')
            d2 = datetime.strptime(all_dates[i], '%Y-%m-%d')
            if (d2 - d1).days == 1:
                streak += 1
            else:
                longest_streak = max(longest_streak, streak)
                streak = 1
        longest_streak = max(longest_streak, streak)

    # Yearly breakdown
    yearly = {}
    yearly_rows = conn.execute(f'''
        SELECT strftime('%Y', date) as year, COUNT(*) as commits,
            COALESCE(SUM(additions), 0) as additions, COALESCE(SUM(deletions), 0) as deletions,
            COUNT(DISTINCT date) as days_active
        FROM commits WHERE username IN ({placeholders})
        GROUP BY strftime('%Y', date) ORDER BY year
    ''', usernames).fetchall()
    for row in yearly_rows:
        yearly[row['year']] = {
            'commits': row['commits'], 'additions': row['additions'], 'deletions': row['deletions'],
            'net_loc': row['additions'] - row['deletions'], 'days_active': row['days_active']
        }

    # Period stats (7d, 30d, 90d, ytd, 1y)
    today = datetime.now()
    year_start = f'{today.year}-01-01'
    periods = {
        '7d': (today - timedelta(days=7)).strftime('%Y-%m-%d'),
        '30d': (today - timedelta(days=30)).strftime('%Y-%m-%d'),
        '90d': (today - timedelta(days=90)).strftime('%Y-%m-%d'),
        'ytd': year_start,
        '1y': (today - timedelta(days=365)).strftime('%Y-%m-%d'),
    }

    period_stats = {}
    for period, start_date in periods.items():
        stats = conn.execute(f'''
            SELECT
                COUNT(*) as commits,
                COALESCE(SUM(additions), 0) as additions,
                COALESCE(SUM(deletions), 0) as deletions,
                COUNT(DISTINCT date) as active_days,
                COUNT(DISTINCT repo) as repos
            FROM commits WHERE username IN ({placeholders}) AND date >= ?
        ''', usernames + [start_date]).fetchone()
        period_stats[period] = dict(stats)

    # 30-day comparison for change calculation
    thirty_days_ago = periods['30d']
    sixty_days_ago = (today - timedelta(days=60)).strftime('%Y-%m-%d')

    prev_30d = conn.execute(f'''
        SELECT COALESCE(SUM(additions), 0) as adds, COALESCE(SUM(deletions), 0) as dels
        FROM commits WHERE username IN ({placeholders}) AND date >= ? AND date < ?
    ''', usernames + [sixty_days_ago, thirty_days_ago]).fetchone()

    # Handle empty result
    if not prev_30d:
        prev_30d = {'adds': 0, 'dels': 0}

    # Calculate percentage change
    additions_30d_change = 0
    deletions_30d_change = 0
    last_30d_adds = period_stats['30d']['additions']
    last_30d_dels = period_stats['30d']['deletions']
    if prev_30d['adds'] > 0:
        additions_30d_change = round(((last_30d_adds - prev_30d['adds']) / prev_30d['adds']) * 100, 0)
    elif last_30d_adds > 0:
        additions_30d_change = 100
    if prev_30d['dels'] > 0:
        deletions_30d_change = round(((last_30d_dels - prev_30d['dels']) / prev_30d['dels']) * 100, 0)
    elif last_30d_dels > 0:
        deletions_30d_change = 100

    conn.close()

    first_date = datetime.strptime(dates['first'], '%Y-%m-%d') if dates['first'] else datetime.now()
    total_days = (datetime.strptime(dates['last'], '%Y-%m-%d') - first_date).days + 1 if dates['first'] else 0
    years_coding = (datetime.now() - first_date).days / 365.25 if dates['first'] else 0

    return {
        'total_commits': total, 'with_loc': with_loc, 'missing_loc': total - with_loc,
        'total_additions': totals['total_additions'], 'total_deletions': totals['total_deletions'],
        'net_loc_change': totals['total_additions'] - totals['total_deletions'],
        'first_commit': dates['first'], 'last_commit': dates['last'],
        'total_days': total_days, 'active_days': active_days, 'unique_repos': unique_repos,
        'years_coding': round(years_coding, 1), 'average_commits': round(total / max(active_days, 1), 1),
        'average_loc_per_commit': round(avg_loc, 0),
        'maximum_commits': max_day['cnt'] if max_day else 0,
        'max_commit_date': max_day['date'] if max_day else None,
        'current_streak': current_streak,
        'longest_streak': longest_streak,
        'most_productive_day': dow_stats[0]['day'] if dow_stats else None,
        'day_of_week_stats': {r['day']: r['commits'] for r in dow_stats},
        'yearly': yearly,
        'periods': period_stats,
        'additions_30d_change': additions_30d_change,
        'deletions_30d_change': deletions_30d_change,
    }


def get_combined_daily(usernames: list) -> list:
    """Get combined daily stats."""
    conn = analyzer.db._get_conn()
    placeholders = ','.join(['?' for _ in usernames])
    rows = conn.execute(f'''
        SELECT date, COUNT(*) as commits, COALESCE(SUM(additions), 0) as additions,
            COALESCE(SUM(deletions), 0) as deletions
        FROM commits WHERE username IN ({placeholders})
        GROUP BY date ORDER BY date
    ''', usernames).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.route('/api/data')
def get_data():
    try:
        all_data = {}
        all_viz = {}

        # Get users from database + configured users
        conn = analyzer.db._get_conn()
        db_users = [r[0] for r in conn.execute('SELECT username FROM users').fetchall()]
        conn.close()

        # Merge: configured users first, then any additional from DB
        users = list(GITHUB_USERS)
        for u in db_users:
            if u not in users:
                users.append(u)

        active_users = []
        for username in users:
            print(f'Loading data for {username}...')
            data = analyzer.get_user_data(username, fetch=False)
            if data.get('stats') and data['stats'].get('total_commits', 0) > 0:
                all_data[username] = data
                all_viz[username] = create_visualizations(data)
                active_users.append(username)

        # Create combined "All" view if multiple users
        if len(active_users) > 1:
            print('Creating combined view...')
            combined_stats = get_combined_stats(active_users)
            combined_daily = get_combined_daily(active_users)
            combined_data = {
                'user': {'username': 'All', 'name': f'Combined ({len(active_users)} users)'},
                'stats': combined_stats,
                'daily': combined_daily,
                'yearly': [{'year': k, **v} for k, v in combined_stats['yearly'].items()],
                'top_repos': [],
                'top_repos_loc': [],
                'monthly': [],
                'recent': []
            }
            all_data['All'] = combined_data
            all_viz['All'] = create_visualizations(combined_data)
            active_users.insert(0, 'All')  # Put "All" first

        return jsonify({
            'success': True,
            'data': all_data,
            'visualizations': all_viz,
            'users': active_users
        })
    except Exception as e:
        import traceback
        if DEBUG:
            traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/user/<username>')
def get_user_data(username):
    try:
        data = analyzer.get_user_data(username)
        viz = create_visualizations(data)
        return jsonify({'success': True, 'data': data, 'visualizations': viz})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/stats')
def get_stats():
    stats = {}
    for username in GITHUB_USERS:
        stats[username] = analyzer.db.get_stats(username)
    return jsonify({'success': True, 'stats': stats})


@app.route('/stats.db')
def serve_stats_db():
    """Serve the SQLite database file for client-side SQL queries."""
    from flask import send_file
    db_path = DB_PATH
    if db_path.exists():
        return send_file(db_path, mimetype='application/octet-stream')
    return jsonify({'error': 'Database not found'}), 404


@app.route('/api/top-repos/<username>')
def get_top_repos(username):
    order = request.args.get('order', 'commits')
    limit = int(request.args.get('limit', 20))
    repos = analyzer.db.get_top_repos(username, limit, order)
    return jsonify({'success': True, 'repos': repos})


@app.route('/api/search/<username>')
def search_commits(username):
    from flask import request
    query = request.args.get('q', '')
    commits = analyzer.db.search_commits(username, query)
    return jsonify({'success': True, 'commits': commits})


@app.route('/api/refresh')
def refresh_data():
    # Clear fetch metadata to force refetch
    conn = analyzer.db._get_conn()
    conn.execute('DELETE FROM fetch_meta')
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Cache cleared'})


@app.route('/api/fetch-loc')
def fetch_more_loc():
    total = 0
    for username in GITHUB_USERS:
        fetched = analyzer.fetch_loc_batch(username, 500)
        total += fetched
    return jsonify({'success': True, 'fetched': total})


@app.route('/api/users')
def list_users():
    """List all users in the database."""
    conn = analyzer.db._get_conn()
    rows = conn.execute('SELECT username, avatar_url, name FROM users').fetchall()
    conn.close()
    users = [{'username': r[0], 'avatar_url': r[1], 'name': r[2]} for r in rows]
    return jsonify({'success': True, 'users': users})


@app.route('/api/fetch/<username>')
def fetch_user(username):
    """Fetch all data for a new user with live progress streaming."""
    from flask import Response

    def generate():
        yield f'data: {{"status": "starting", "message": "Fetching profile for {username}..."}}\n\n'

        # Fetch user profile
        user_data = GitHubAPI.get_user_profile(username)
        if not user_data:
            yield f'data: {{"status": "error", "message": "User {username} not found"}}\n\n'
            return

        # Save user
        analyzer.db.save_user(user_data)
        name = user_data.get("name", username)
        yield f'data: {{"status": "progress", "message": "Profile saved: {name}"}}\n\n'

        # Fetch commits
        yield f'data: {{"status": "progress", "message": "Fetching commits since {START_DATE}..."}}\n\n'

        end_date = datetime.now().date()
        current = START_DATE
        total_commits = 0

        while current <= end_date:
            year, month = current.year, current.month
            yield f'data: {{"status": "progress", "message": "Fetching {year}-{month:02d}..."}}\n\n'

            commits = analyzer.fetch_commits_for_month(username, year, month)
            saved = analyzer.db.save_commits(commits)
            total_commits += len(commits)
            yield f'data: {{"status": "progress", "message": "  Found {len(commits)} commits"}}\n\n'

            analyzer.db.mark_month_fetched(username, year, month)

            if month == 12:
                current = date(year + 1, 1, 1)
            else:
                current = date(year, month + 1, 1)

        stats = analyzer.db.get_stats(username)
        total = stats.get('total_commits', 0)
        yield f'data: {{"status": "progress", "message": "Total commits: {total}"}}\n\n'

        # Fetch some LOC data
        yield f'data: {{"status": "progress", "message": "Fetching LOC data..."}}\n\n'
        fetched = analyzer.fetch_loc_batch(username, 100)
        yield f'data: {{"status": "progress", "message": "Fetched LOC for {fetched} commits"}}\n\n'

        yield f'data: {{"status": "complete", "message": "Fetch complete for {username}"}}\n\n'

    return Response(generate(), mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/fetch-status')
def fetch_status():
    """Get current fetch status showing what data exists vs needs fetching."""
    conn = analyzer.db._get_conn()

    status = {}
    for username in GITHUB_USERS:
        total = conn.execute('SELECT COUNT(*) FROM commits WHERE username = ?', (username,)).fetchone()[0]
        with_loc = conn.execute('SELECT COUNT(*) FROM commits WHERE username = ? AND additions IS NOT NULL', (username,)).fetchone()[0]
        without_loc = total - with_loc

        # LOC totals
        totals = conn.execute('''
            SELECT COALESCE(SUM(additions), 0) as adds, COALESCE(SUM(deletions), 0) as dels
            FROM commits WHERE username = ? AND additions IS NOT NULL
        ''', (username,)).fetchone()

        status[username] = {
            'total_commits': total,
            'with_loc': with_loc,
            'without_loc': without_loc,
            'loc_percent': round(100 * with_loc / max(total, 1), 1),
            'total_additions': totals['adds'],
            'total_deletions': totals['dels'],
            'net_loc': totals['adds'] - totals['dels']
        }

    # Combined stats
    total = conn.execute('SELECT COUNT(*) FROM commits').fetchone()[0]
    with_loc = conn.execute('SELECT COUNT(*) FROM commits WHERE additions IS NOT NULL').fetchone()[0]
    totals = conn.execute('SELECT COALESCE(SUM(additions), 0), COALESCE(SUM(deletions), 0) FROM commits WHERE additions IS NOT NULL').fetchone()

    status['_combined'] = {
        'total_commits': total,
        'with_loc': with_loc,
        'without_loc': total - with_loc,
        'loc_percent': round(100 * with_loc / max(total, 1), 1),
        'total_additions': totals[0],
        'total_deletions': totals[1],
        'net_loc': totals[0] - totals[1]
    }

    conn.close()
    return jsonify({'success': True, 'status': status})


@app.route('/api/fetch-loc-stream')
def fetch_loc_stream():
    """Stream LOC fetch progress."""
    from flask import Response

    def generate():
        conn = analyzer.db._get_conn()

        # Get commits needing LOC
        rows = conn.execute('''
            SELECT sha, repo FROM commits
            WHERE additions IS NULL
            ORDER BY date DESC
            LIMIT 500
        ''').fetchall()
        conn.close()

        total = len(rows)
        yield f'data: {{"status": "starting", "total": {total}}}\n\n'

        success = 0
        errors = 0

        for i, row in enumerate(rows):
            sha, repo = row['sha'], row['repo']

            try:
                resp = requests.get(
                    f'https://api.github.com/repos/{repo}/commits/{sha}',
                    headers=REST_HEADERS, timeout=10
                )
                if resp.status_code == 200:
                    stats = resp.json().get('stats', {})
                    adds = stats.get('additions', 0)
                    dels = stats.get('deletions', 0)

                    conn = analyzer.db._get_conn()
                    conn.execute('UPDATE commits SET additions=?, deletions=? WHERE sha=?', (adds, dels, sha))
                    conn.commit()
                    conn.close()
                    success += 1
                else:
                    errors += 1
            except:
                errors += 1

            if (i + 1) % 20 == 0:
                yield f'data: {{"status": "progress", "processed": {i+1}, "total": {total}, "success": {success}, "errors": {errors}}}\n\n'

            time.sleep(0.1)  # Rate limit

        yield f'data: {{"status": "complete", "success": {success}, "errors": {errors}}}\n\n'

    return Response(generate(), mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/fetch-languages')
def fetch_languages_api():
    """Fetch language stats for all repos."""
    from flask import Response

    def generate():
        # Get all unique repos
        conn = analyzer.db._get_conn()
        repos = conn.execute('''
            SELECT DISTINCT repo, username FROM commits
            WHERE repo IS NOT NULL AND repo != ''
        ''').fetchall()
        conn.close()

        # Check which ones need fetching
        need_fetch = []
        for row in repos:
            repo, username = row['repo'], row['username']
            conn = analyzer.db._get_conn()
            existing = conn.execute('SELECT 1 FROM languages WHERE repo = ?', (repo,)).fetchone()
            conn.close()
            if not existing:
                need_fetch.append((repo, username))

        total = len(need_fetch)
        yield f'data: {{"status": "starting", "total": {total}}}\n\n'

        success = 0
        errors = 0

        for i, (repo, username) in enumerate(need_fetch):
            try:
                resp = requests.get(
                    f'https://api.github.com/repos/{repo}/languages',
                    headers=REST_HEADERS, timeout=10
                )
                if resp.status_code == 200:
                    languages = resp.json()
                    conn = analyzer.db._get_conn()
                    for lang, bytes_count in languages.items():
                        conn.execute('''
                            INSERT OR REPLACE INTO languages (repo, username, language, bytes, fetched_at)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (repo, username, lang, bytes_count, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    success += 1
                else:
                    errors += 1
            except:
                errors += 1

            if (i + 1) % 10 == 0:
                yield f'data: {{"status": "progress", "processed": {i+1}, "total": {total}, "success": {success}, "errors": {errors}}}\n\n'

            time.sleep(0.1)  # Rate limit

        yield f'data: {{"status": "complete", "success": {success}, "errors": {errors}}}\n\n'

    return Response(generate(), mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def export_static_site(output_dir: Path):
    """Export static HTML dashboard."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f'\nExporting static site to {output_dir}...')

    all_data = {}
    all_viz = {}
    active_users = []

    for username in GITHUB_USERS:
        data = analyzer.get_user_data(username)
        if data.get('stats') and data['stats'].get('total_commits', 0) > 0:
            all_data[username] = data
            all_viz[username] = create_visualizations(data)
            active_users.append(username)

    # Create combined "All" view if multiple users
    if len(active_users) > 1:
        print('Creating combined view...')
        combined_stats = get_combined_stats(active_users)
        combined_daily = get_combined_daily(active_users)
        combined_data = {
            'user': {'username': 'All', 'name': f'Combined ({len(active_users)} users)'},
            'stats': combined_stats,
            'daily': combined_daily,
            'yearly': [{'year': k, **v} for k, v in combined_stats['yearly'].items()],
            'top_repos': [],
            'top_repos_loc': [],
            'monthly': [],
            'recent': []
        }
        all_data['All'] = combined_data
        all_viz['All'] = create_visualizations(combined_data)
        active_users.insert(0, 'All')

    static_data = {
        'success': True,
        'data': all_data,
        'visualizations': all_viz,
        'users': active_users
    }
    
    # Read template
    template_path = Path(__file__).parent / 'templates' / 'index.html'
    with open(template_path) as f:
        html = f.read()
    
    # Inject data
    data_json = json.dumps(static_data, default=str)
    injection = f'<script>window.STATIC_DATA = {data_json};</script>\n'
    html = html.replace('<script>', injection + '<script>', 1)
    
    # Modify initApp fetch to use static data (be specific to avoid breaking initSqlDatabase)
    html_before = html
    html = html.replace(
        "const response = await fetch('/api/data');",
        "if (window.STATIC_DATA) { allData = window.STATIC_DATA; setupUserButtons(allData.users, allData.data); updateView(); return; }\n            const response = await fetch('/api/data');"
    )
    if html == html_before:
        print("  Warning: Static data injection pattern not found!")
    
    # Update title
    user_names = [u for u in active_users if u != 'All']
    html = html.replace('<title>GitHub Stats</title>',
                       f'<title>GitHub Stats - {" & ".join(user_names)}</title>')
    
    # Update footer with generation date
    html = html.replace('github.com/zeekay/stats</a></p>',
        f'github.com/zeekay/stats</a> | Generated {datetime.now().strftime("%Y-%m-%d")}</p>')
    
    # Write files
    (output_dir / 'index.html').write_text(html)
    (output_dir / 'data.json').write_text(json.dumps(static_data, default=str, indent=2))
    
    print(f'  Created {output_dir}/index.html')
    print(f'  Created {output_dir}/data.json')
    print(f'\nPreview: python -m http.server -d {output_dir} 8000')


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='GitHub Stats - Analyze your contribution history')
    parser.add_argument('--export', type=str, metavar='DIR', help='Export static site')
    parser.add_argument('--fetch-loc', action='store_true', help='Fetch all LOC data')
    parser.add_argument('--stats', action='store_true', help='Show statistics')
    parser.add_argument('--migrate', action='store_true', help='Migrate from JSONL to SQLite')
    
    args = parser.parse_args()
    
    print(f'\nGitHub Stats')
    print(f'Users: {GITHUB_USERS}')
    print(f'Since: {START_DATE}')
    print(f'Database: {DB_PATH}')
    
    if args.stats:
        for username in GITHUB_USERS:
            stats = analyzer.db.get_stats(username)
            user = analyzer.db.get_user(username)
            print(f'\n{username}:')
            if user:
                print(f'  Avatar: {user.get("avatar_url", "N/A")}')
            print(f'  Commits: {stats["total_commits"]:,}')
            print(f'  With LOC: {stats["with_loc"]:,} ({100*stats["with_loc"]/max(stats["total_commits"],1):.1f}%)')
            print(f'  Total +{stats["total_additions"]:,} / -{stats["total_deletions"]:,}')
            print(f'  Net: {stats["net_loc"]:+,}')
    
    elif args.fetch_loc:
        for username in GITHUB_USERS:
            print(f'\n{username}:')
            while True:
                fetched = analyzer.fetch_loc_batch(username, 500)
                if fetched == 0:
                    print('  Done')
                    break
    
    elif args.migrate:
        # Migrate from JSONL
        from pathlib import Path
        cache_dir = Path('./cache')
        for username in GITHUB_USERS:
            user_dir = cache_dir / username
            if not user_dir.exists():
                continue
            
            print(f'\nMigrating {username}...')
            commits = []
            
            # Load from JSONL files
            commits_dir = user_dir / 'commits'
            loc_dir = user_dir / 'loc'
            
            if commits_dir.exists():
                for f in commits_dir.glob('*.jsonl'):
                    with open(f) as fp:
                        for line in fp:
                            try:
                                c = json.loads(line.strip())
                                c['username'] = username
                                commits.append(c)
                            except:
                                pass
            
            # Load LOC data
            loc_data = {}
            if loc_dir.exists():
                for f in loc_dir.glob('*.jsonl'):
                    with open(f) as fp:
                        for line in fp:
                            try:
                                d = json.loads(line.strip())
                                loc_data[d['sha']] = (d.get('additions'), d.get('deletions'))
                            except:
                                pass
            
            # Merge LOC data
            for c in commits:
                if c['sha'] in loc_data:
                    c['additions'], c['deletions'] = loc_data[c['sha']]
            
            # Save to SQLite
            analyzer.db.save_commits(commits)
            print(f'  Migrated {len(commits)} commits')
    
    elif args.export:
        export_static_site(Path(args.export))
    
    else:
        print(f'Port: {PORT}\n')
        if DEBUG:
            app.run(debug=True, port=PORT)
        else:
            import waitress
            waitress.serve(app, host='0.0.0.0', port=PORT)
