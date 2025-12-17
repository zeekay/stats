#!/usr/bin/env python3

import os
import json
from datetime import datetime, timedelta, date
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify
import requests
import pandas as pd
import numpy as np
import plotly.graph_objs as go
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import time

load_dotenv()

app = Flask(__name__)

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
GITHUB_USERS = [user.strip() for user in os.getenv('GITHUB_USERS', 'zeekay').split(',')]
DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'
PORT = int(os.getenv('PORT', '5000'))
START_DATE = datetime.strptime(os.getenv('START_DATE', '2021-01-01'), '%Y-%m-%d').date()
CACHE_DIR = Path(os.getenv('CACHE_DIR', './cache'))

HEADERS = {'Authorization': f'bearer {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
REST_HEADERS = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
SEARCH_HEADERS = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.cloak-preview'}
REQUEST_DELAY = 0.1

# Ensure cache directory exists
CACHE_DIR.mkdir(exist_ok=True)


class CommitCache:
    """Persistent cache for commits"""
    
    def __init__(self, username):
        self.username = username
        self.cache_file = CACHE_DIR / f"{username}_commits.json"
        self.meta_file = CACHE_DIR / f"{username}_meta.json"
        self.commits = {}  # sha -> commit data
        self.meta = {'last_fetch': None, 'months_fetched': []}
        self._load()
    
    def _load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file) as f:
                    self.commits = json.load(f)
                print(f"  Loaded {len(self.commits)} cached commits for {self.username}")
            except:
                self.commits = {}
        
        if self.meta_file.exists():
            try:
                with open(self.meta_file) as f:
                    self.meta = json.load(f)
            except:
                self.meta = {'last_fetch': None, 'months_fetched': []}
    
    def save(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self.commits, f)
        with open(self.meta_file, 'w') as f:
            json.dump(self.meta, f)
        print(f"  Saved {len(self.commits)} commits to cache")
    
    def add_commits(self, commits_list):
        for commit in commits_list:
            sha = commit.get('sha')
            if sha and sha not in self.commits:
                self.commits[sha] = commit
    
    def mark_month_fetched(self, year, month):
        key = f"{year}-{month:02d}"
        if key not in self.meta['months_fetched']:
            self.meta['months_fetched'].append(key)
        self.meta['last_fetch'] = datetime.now().isoformat()
    
    def is_month_fetched(self, year, month):
        key = f"{year}-{month:02d}"
        return key in self.meta['months_fetched']
    
    def get_all_commits(self):
        return list(self.commits.values())


class GitHubContributionAnalyzer:
    def __init__(self):
        self.caches = {}

    def _get_cache(self, username):
        if username not in self.caches:
            self.caches[username] = CommitCache(username)
        return self.caches[username]

    def _search_commits_for_range(self, username, start_date, end_date):
        """Search for commits by author in a date range"""
        commits = []
        page = 1

        query = f'author:{username} committer-date:{start_date}..{end_date}'

        while page <= 10:
            url = 'https://api.github.com/search/commits'
            params = {'q': query, 'per_page': 100, 'page': page, 'sort': 'committer-date'}

            try:
                resp = requests.get(url, headers=SEARCH_HEADERS, params=params)

                if resp.status_code == 403:
                    reset_time = int(resp.headers.get('X-RateLimit-Reset', 0))
                    wait_time = max(reset_time - time.time(), 60)
                    print(f"    Rate limited, waiting {wait_time:.0f}s...")
                    time.sleep(wait_time)
                    continue

                if resp.status_code != 200:
                    print(f"    Search error: {resp.status_code}")
                    break

                data = resp.json()
                items = data.get('items', [])
                total_count = data.get('total_count', 0)

                if not items:
                    break

                for item in items:
                    commit_obj = item.get('commit', {})
                    committer = commit_obj.get('committer', {})
                    commit_date = committer.get('date', '')[:10]

                    commits.append({
                        'sha': item.get('sha', ''),
                        'date': commit_date,
                        'repo': item.get('repository', {}).get('full_name', ''),
                        'message': commit_obj.get('message', '').split('\n')[0][:100],
                        'url': item.get('url', ''),
                        'additions': None,
                        'deletions': None
                    })

                if len(items) < 100:
                    break

                page += 1
                time.sleep(REQUEST_DELAY * 2)

            except Exception as e:
                print(f"    Search error: {e}")
                break

        return commits, len(commits) >= 1000

    def _search_commits_for_month(self, username, year, month):
        """Search for commits by author in a specific month, splitting if needed"""
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)

        # First try the whole month
        commits, hit_limit = self._search_commits_for_range(username, start_date, end_date)

        if hit_limit:
            # Split month into weeks if we hit the limit
            print(f"    Hit 1000 limit, splitting into weeks...")
            commits = []
            week_start = start_date

            while week_start <= end_date:
                week_end = min(week_start + timedelta(days=6), end_date)
                week_commits, week_hit_limit = self._search_commits_for_range(username, week_start, week_end)

                if week_hit_limit:
                    # Split week into days
                    print(f"    Week {week_start} hit limit, splitting into days...")
                    for day_offset in range(7):
                        day = week_start + timedelta(days=day_offset)
                        if day > end_date:
                            break
                        day_commits, _ = self._search_commits_for_range(username, day, day)
                        commits.extend(day_commits)
                        time.sleep(REQUEST_DELAY)
                else:
                    commits.extend(week_commits)

                week_start = week_end + timedelta(days=1)
                time.sleep(REQUEST_DELAY)

        return commits

    def _fetch_commit_stats(self, commit):
        """Fetch additions/deletions for a single commit"""
        if commit.get('additions') is not None:
            return commit
            
        url = commit.get('url')
        if not url:
            return commit
            
        try:
            resp = requests.get(url, headers=REST_HEADERS)
            if resp.status_code == 200:
                data = resp.json()
                stats = data.get('stats', {})
                commit['additions'] = stats.get('additions', 0)
                commit['deletions'] = stats.get('deletions', 0)
        except:
            pass
        
        return commit

    def _fetch_contribution_calendar(self, username, start_date):
        """Fetch contribution calendar via GraphQL"""
        end_date = datetime.now().date()
        all_contributions = {}

        query = '''
        query($userName: String!, $from: DateTime!, $to: DateTime!) {
            user(login: $userName) {
                contributionsCollection(from: $from, to: $to) {
                    contributionCalendar {
                        totalContributions
                        weeks { contributionDays { date contributionCount } }
                    }
                }
            }
        }
        '''

        current_start = start_date
        while current_start < end_date:
            current_end = min(current_start + relativedelta(years=1) - timedelta(days=1), end_date)

            variables = {
                "userName": username,
                "from": current_start.isoformat() + "T00:00:00Z",
                "to": current_end.isoformat() + "T23:59:59Z"
            }

            try:
                response = requests.post('https://api.github.com/graphql', headers=HEADERS,
                                       json={'query': query, 'variables': variables}, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    if 'errors' not in data:
                        user_data = data.get('data', {}).get('user', {})
                        collection = user_data.get('contributionsCollection', {})
                        calendar = collection.get('contributionCalendar', {})
                        for week in calendar.get('weeks', []):
                            for day in week.get('contributionDays', []):
                                all_contributions[day['date']] = day['contributionCount']
            except:
                pass

            current_start = current_end + timedelta(days=1)
            time.sleep(REQUEST_DELAY)

        return all_contributions

    def fetch_and_cache_commits(self, username, since_date=None):
        """Incrementally fetch and cache commits"""
        if since_date is None:
            since_date = START_DATE
            
        cache = self._get_cache(username)
        end_date = datetime.now().date()
        
        # Find months that need fetching
        current = since_date
        months_to_fetch = []
        
        while current <= end_date:
            year, month = current.year, current.month
            # Always refetch current month
            if not cache.is_month_fetched(year, month) or (year == end_date.year and month == end_date.month):
                months_to_fetch.append((year, month))
            
            if month == 12:
                current = date(year + 1, 1, 1)
            else:
                current = date(year, month + 1, 1)
        
        print(f"  {len(months_to_fetch)} months to fetch")
        
        # Fetch commits for each month
        for year, month in months_to_fetch:
            print(f"  Fetching {year}-{month:02d}...")
            commits = self._search_commits_for_month(username, year, month)
            print(f"    Found {len(commits)} commits")
            cache.add_commits(commits)
            cache.mark_month_fetched(year, month)
            cache.save()
            time.sleep(REQUEST_DELAY)
        
        return cache.get_all_commits()

    def fetch_loc_for_commits(self, username, max_commits=500):
        """Fetch LOC stats for commits that don't have them"""
        cache = self._get_cache(username)
        
        # Find commits missing LOC data
        commits_needing_loc = [c for c in cache.commits.values() if c.get('additions') is None]
        print(f"  {len(commits_needing_loc)} commits need LOC data")
        
        # Fetch LOC for a batch
        fetched = 0
        for commit in commits_needing_loc[:max_commits]:
            self._fetch_commit_stats(commit)
            cache.commits[commit['sha']] = commit
            fetched += 1
            
            if fetched % 50 == 0:
                cache.save()
                print(f"    Fetched {fetched}/{min(len(commits_needing_loc), max_commits)} LOC stats")
            
            time.sleep(REQUEST_DELAY)
        
        cache.save()
        return fetched

    def get_user_contributions(self, username, since_date=None):
        if since_date is None:
            since_date = START_DATE

        print(f"\nFetching contributions for {username} since {since_date}...")

        # Get contribution calendar
        contribution_counts = self._fetch_contribution_calendar(username, since_date)
        print(f"  Got {len(contribution_counts)} days of contribution calendar data")

        # Fetch and cache commits
        all_commits = self.fetch_and_cache_commits(username, since_date)
        print(f"  Total cached commits: {len(all_commits)}")

        # Optionally fetch more LOC data
        self.fetch_loc_for_commits(username, max_commits=200)

        # Build daily stats
        daily_stats = defaultdict(lambda: {'commits': 0, 'additions': 0, 'deletions': 0, 'repos': set(), 'contributions': 0})
        
        for commit in all_commits:
            commit_date = commit.get('date', '')
            if commit_date >= since_date.isoformat():
                daily_stats[commit_date]['commits'] += 1
                if commit.get('additions') is not None:
                    daily_stats[commit_date]['additions'] += commit['additions']
                    daily_stats[commit_date]['deletions'] += commit.get('deletions', 0)
                if commit.get('repo'):
                    daily_stats[commit_date]['repos'].add(commit['repo'])

        for date_str, count in contribution_counts.items():
            daily_stats[date_str]['contributions'] = count

        # Build contributions array
        contributions = []
        end_date = datetime.now().date()
        current_date = since_date
        
        while current_date <= end_date:
            date_str = current_date.isoformat()
            stats = daily_stats.get(date_str, {'commits': 0, 'additions': 0, 'deletions': 0, 'repos': set(), 'contributions': 0})
            contributions.append({
                'date': date_str,
                'contributions': stats.get('contributions', 0) or contribution_counts.get(date_str, 0),
                'commits': stats['commits'],
                'additions': stats['additions'],
                'deletions': stats['deletions'],
                'net_loc': stats['additions'] - stats['deletions'],
                'repos_count': len(stats.get('repos', set())),
                'username': username
            })
            current_date += timedelta(days=1)

        return contributions

    def analyze_growth(self, contributions):
        if not contributions:
            return {}
        df = pd.DataFrame(contributions)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        for col in ['contributions', 'commits', 'additions', 'deletions', 'net_loc']:
            df[f'{col}_7d_avg'] = df[col].rolling(window=7, min_periods=1).mean()
            df[f'{col}_30d_avg'] = df[col].rolling(window=30, min_periods=1).mean()
        
        x_data = np.arange(len(df))
        y_data = df['commits'].values
        
        try:
            first_half_avg = y_data[:len(y_data)//2].mean()
            second_half_avg = y_data[len(y_data)//2:].mean()
            growth_rate = np.log(second_half_avg / first_half_avg) / (len(y_data)/2) if first_half_avg > 0 else 0
            df['exponential_fit'] = y_data.mean() * np.exp(growth_rate * x_data)
        except:
            df['exponential_fit'] = df['commits']
            growth_rate = 0
            
        def safe_float(val): return float(val) if not pd.isna(val) else 0.0
        def safe_int(val): return int(val) if not pd.isna(val) else 0
        
        last_30 = df.tail(30)
        prev_30 = df.iloc[-60:-30] if len(df) >= 60 else df.head(30)
        
        def calc_change(current, previous):
            if previous == 0: return 100.0 if current > 0 else 0.0
            return ((current - previous) / previous) * 100

        df['year'] = df['date'].dt.year
        yearly_stats = {}
        for year in df['year'].unique():
            year_data = df[df['year'] == year]
            yearly_stats[int(year)] = {
                'commits': safe_int(year_data['commits'].sum()),
                'additions': safe_int(year_data['additions'].sum()),
                'deletions': safe_int(year_data['deletions'].sum()),
                'net_loc': safe_int(year_data['net_loc'].sum()),
                'contributions': safe_int(year_data['contributions'].sum()),
                'days_active': safe_int((year_data['commits'] > 0).sum())
            }

        total_days = len(df)
        
        stats = {
            'total_contributions': safe_int(df['contributions'].sum()),
            'total_commits': safe_int(df['commits'].sum()),
            'total_additions': safe_int(df['additions'].sum()),
            'total_deletions': safe_int(df['deletions'].sum()),
            'net_loc_change': safe_int(df['net_loc'].sum()),
            'total_days': total_days,
            'days_with_commits': safe_int((df['commits'] > 0).sum()),
            'average_contributions': safe_float(df['contributions'].mean()),
            'average_commits': safe_float(df['commits'].mean()),
            'average_additions': safe_float(df['additions'].mean()),
            'average_deletions': safe_float(df['deletions'].mean()),
            'average_net_loc': safe_float(df['net_loc'].mean()),
            'maximum_contributions': safe_int(df['contributions'].max()),
            'maximum_commits': safe_int(df['commits'].max()),
            'maximum_additions': safe_int(df['additions'].max()),
            'peak_periods_count': safe_int(len(df[df['commits'] > df['commits'].quantile(0.9)])),
            'growth_rate': safe_float(growth_rate * 100),
            'exponential_params': [1.0, safe_float(growth_rate), 0.0],
            'commits_30d_change': safe_float(calc_change(last_30['commits'].sum(), prev_30['commits'].sum())),
            'additions_30d_change': safe_float(calc_change(last_30['additions'].sum(), prev_30['additions'].sum())),
            'deletions_30d_change': safe_float(calc_change(last_30['deletions'].sum(), prev_30['deletions'].sum())),
            'last_7d_commits': safe_int(df.tail(7)['commits'].sum()),
            'last_7d_additions': safe_int(df.tail(7)['additions'].sum()),
            'last_7d_deletions': safe_int(df.tail(7)['deletions'].sum()),
            'last_30d_commits': safe_int(df.tail(30)['commits'].sum()),
            'last_30d_additions': safe_int(df.tail(30)['additions'].sum()),
            'last_30d_deletions': safe_int(df.tail(30)['deletions'].sum()),
            'yearly': yearly_stats,
        }
        
        def clean_record(record):
            clean = {}
            for key, value in record.items():
                if pd.isna(value): clean[key] = None
                elif isinstance(value, (np.integer, np.int64)): clean[key] = int(value)
                elif isinstance(value, (np.floating, np.float64)): clean[key] = float(value)
                elif isinstance(value, pd.Timestamp): clean[key] = value.isoformat()
                else: clean[key] = value
            return clean
            
        clean_data = [clean_record(record) for record in df.to_dict('records')]
        return {'data': clean_data, 'stats': stats}

    def get_all_users_data(self):
        all_data = {}
        for username in GITHUB_USERS:
            contributions = self.get_user_contributions(username)
            analysis = self.analyze_growth(contributions)
            all_data[username] = analysis
        return all_data


def create_visualizations(data):
    visualizations = {}
    for username, user_data in data.items():
        if not user_data.get('data'):
            continue
        df = pd.DataFrame(user_data['data'])
        df['date'] = pd.to_datetime(df['date'])
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits'], mode='lines', name='Commits', line=dict(color='#667eea', width=1), opacity=0.5))
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits_7d_avg'], mode='lines', name='7-Day Avg', line=dict(color='#667eea', width=2)))
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits_30d_avg'], mode='lines', name='30-Day Avg', line=dict(color='#f5576c', width=2)))
        fig.update_layout(title=f'{username} - Commits Over Time', xaxis_title='Date', yaxis_title='Commits', hovermode='x unified', template='plotly_white', height=400)
        try: visualizations[f'{username}_timeseries'] = fig.to_json()
        except Exception as e: visualizations[f'{username}_timeseries'] = json.dumps({"error": str(e)})
        
        loc_fig = go.Figure()
        loc_fig.add_trace(go.Bar(x=df['date'], y=df['additions'], name='Additions', marker_color='#2ecc71', opacity=0.7))
        loc_fig.add_trace(go.Bar(x=df['date'], y=[-d for d in df['deletions']], name='Deletions', marker_color='#e74c3c', opacity=0.7))
        loc_fig.add_trace(go.Scatter(x=df['date'], y=df['net_loc_30d_avg'], mode='lines', name='Net LOC (30d avg)', line=dict(color='#3498db', width=3)))
        loc_fig.update_layout(title=f'{username} - Lines of Code Changes', xaxis_title='Date', yaxis_title='Lines of Code', barmode='relative', hovermode='x unified', template='plotly_white', height=400)
        try: visualizations[f'{username}_loc'] = loc_fig.to_json()
        except Exception as e: visualizations[f'{username}_loc'] = json.dumps({"error": str(e)})
        
        df['year'] = df['date'].dt.year
        df['month'] = df['date'].dt.month
        heatmap_data = df.pivot_table(values='commits', index='year', columns='month', aggfunc='sum', fill_value=0)
        heatmap_z = heatmap_data.values.tolist()
        heatmap_fig = go.Figure(data=go.Heatmap(
            z=heatmap_z, 
            x=['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
            y=[str(y) for y in heatmap_data.index.tolist()],
            colorscale='Viridis', 
            colorbar=dict(title='Commits')
        ))
        heatmap_fig.update_layout(title=f'{username} - Commits by Year/Month', xaxis_title='Month', yaxis_title='Year', height=350)
        try: visualizations[f'{username}_heatmap'] = heatmap_fig.to_json()
        except Exception as e: visualizations[f'{username}_heatmap'] = json.dumps({"error": str(e)})
        
    return visualizations


analyzer = GitHubContributionAnalyzer()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/data')
def get_data():
    try:
        data = analyzer.get_all_users_data()
        visualizations = create_visualizations(data)
        return jsonify({'success': True, 'data': data, 'visualizations': visualizations, 'users': GITHUB_USERS})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/user/<username>')
def get_user_data(username):
    try:
        contributions = analyzer.get_user_contributions(username)
        analysis = analyzer.analyze_growth(contributions)
        return jsonify({'success': True, 'data': analysis})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/refresh')
def refresh_data():
    for cache in analyzer.caches.values():
        cache.meta['months_fetched'] = []
    return jsonify({'success': True, 'message': 'Cache metadata cleared, will refetch'})


@app.route('/api/fetch-more-loc')
def fetch_more_loc():
    """Endpoint to fetch more LOC data for cached commits"""
    total_fetched = 0
    for username in GITHUB_USERS:
        fetched = analyzer.fetch_loc_for_commits(username, max_commits=500)
        total_fetched += fetched
    return jsonify({'success': True, 'fetched': total_fetched})


def export_static_site(output_dir: Path):
    """Export a static HTML dashboard that can be hosted on GitHub Pages."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\nExporting static site to {output_dir}...")
    
    # Fetch all data
    data = analyzer.get_all_users_data()
    visualizations = create_visualizations(data)
    
    # Read the template
    template_path = Path(__file__).parent / 'templates' / 'index.html'
    with open(template_path) as f:
        html_template = f.read()
    
    # Create static HTML with embedded data
    static_data = {
        'success': True,
        'data': data,
        'visualizations': visualizations,
        'users': GITHUB_USERS
    }
    
    # Embed the data directly into the HTML
    data_json = json.dumps(static_data, default=str)
    
    # Inject data and modify script to use it
    injection_script = f'''
    <script>
        // Pre-loaded data (exported {datetime.now().strftime('%Y-%m-%d %H:%M')})
        window.STATIC_DATA = {data_json};
    </script>
    '''
    
    # Insert before the main script
    static_html = html_template.replace(
        '<script>\n        let currentUser = null;',
        injection_script + '\n    <script>\n        let currentUser = null;'
    )
    
    # Modify initApp to use static data
    static_html = static_html.replace(
        '''async function initApp() {
            showLoading();
            try {
                const response = await fetch('/api/data');
                const data = await response.json();

                if (data.success) {
                    allData = data;
                    setupUserButtons(data.users);
                    if (data.users.length > 0) {
                        selectUser(data.users[0]);
                    }
                } else {
                    showError(data.error || 'Failed to fetch data');
                }
            } catch (error) {
                showError('Network error: ' + error.message);
            }
        }''',
        '''async function initApp() {
            // Use pre-loaded static data
            if (window.STATIC_DATA) {
                allData = window.STATIC_DATA;
                setupUserButtons(allData.users);
                if (allData.users.length > 0) {
                    selectUser(allData.users[0]);
                }
                return;
            }
            // Fallback to API fetch for server mode
            showLoading();
            try {
                const response = await fetch('/api/data');
                const data = await response.json();
                if (data.success) {
                    allData = data;
                    setupUserButtons(data.users);
                    if (data.users.length > 0) {
                        selectUser(data.users[0]);
                    }
                } else {
                    showError(data.error || 'Failed to fetch data');
                }
            } catch (error) {
                showError('Network error: ' + error.message);
            }
        }'''
    )
    
    # Update title
    users_str = ' & '.join(GITHUB_USERS)
    static_html = static_html.replace(
        '<title>GitHub Contribution Analyzer</title>',
        f'<title>GitHub Stats - {users_str}</title>'
    )
    
    # Add footer
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    static_html = static_html.replace(
        '</body>',
        f'''<footer style="text-align:center;padding:20px;opacity:0.5;font-size:0.8rem;">
        Generated {generated_at} | <a href="https://github.com/zeekay/stats" style="color:#667eea;">GitHub Stats</a>
    </footer>
</body>'''
    )
    
    # Write output
    output_file = output_dir / 'index.html'
    with open(output_file, 'w') as f:
        f.write(static_html)
    
    # Also export raw data
    data_file = output_dir / 'data.json'
    with open(data_file, 'w') as f:
        json.dump(static_data, f, default=str, indent=2)
    
    print(f"  Created {output_file}")
    print(f"  Created {data_file}")
    print(f"\nStatic site exported!")
    print(f"Preview: python -m http.server -d {output_dir} 8000")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='GitHub Contribution Analyzer')
    parser.add_argument('--export', type=str, metavar='DIR',
                       help='Export static HTML dashboard to directory')
    parser.add_argument('--fetch-loc', action='store_true',
                       help='Fetch LOC data for all cached commits')
    parser.add_argument('--refresh', action='store_true',
                       help='Clear cache metadata and refetch')
    
    args = parser.parse_args()
    
    print(f"\nGitHub Contribution Analyzer")
    print(f"Users: {GITHUB_USERS}")
    print(f"Since: {START_DATE}")
    print(f"Cache: {CACHE_DIR}")
    
    if args.refresh:
        for username in GITHUB_USERS:
            cache = analyzer._get_cache(username)
            cache.meta['months_fetched'] = []
            cache.save()
        print("Cache cleared. Will refetch on next run.")
    
    if args.fetch_loc:
        print("\nFetching LOC data...")
        for username in GITHUB_USERS:
            print(f"\n{username}:")
            while True:
                fetched = analyzer.fetch_loc_for_commits(username, max_commits=500)
                if fetched == 0:
                    print(f"  Done - all LOC fetched")
                    break
    
    elif args.export:
        export_static_site(Path(args.export))
    
    else:
        print(f"Port: {PORT}\n")
        if DEBUG:
            app.run(debug=True, port=PORT)
        else:
            import waitress
            waitress.serve(app, host='0.0.0.0', port=PORT)
