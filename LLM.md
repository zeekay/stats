# Hanzo Stats - GitHub Statistics SDK

## Overview
Flask web app that shows **real GitHub contribution statistics** including actual lines of code (LOC) added/deleted. Features **SQL and GraphQL query interfaces** for data exploration. Deployed as a static site to GitHub Pages with sql.js for in-browser queries.

**Live Site**: https://zeekay.github.io/stats/

## Related Packages (Dec 2024)

### @hanzo/stats (`~/work/hanzo/stats/`)
SQL-powered stats dashboard library with client-side SQLite, chart configs, and data providers.
- **StatsDB**: sql.js wrapper for browser-side SQLite queries
- **Hooks**: `useStatsDB`, `useQuery`, `useGitHubStats`, `useAIStats`
- **Components**: `StatCard`, `StatsGrid`, `SQLConsole`, `QueryResults`
- **Providers**: GitHub, AI, StackOverflow, Spotify, SoundCloud
- **Charts**: Default chart configs with Recharts/Plotly themes

### @hanzo/home (`~/work/hanzo/home/`)
Forkable Next.js 15 personal homepage template using @hanzo/stats.
- **Features**: GitHub stats, AI usage, music embeds, SQL console
- **Config**: `stats.config.ts` for personalization
- **Components**: Hero, Tabs, OverviewTab, CodeTab, MusicTab, SocialTab, QueryTab
- **API Routes**: `/api/github/stats`, `/api/ai/stats`
- **Sync Script**: `npm run sync` fetches GitHub data to stats.db

## Current Stats (Dec 2024)
- **37,047 total commits** (zeekay: 31,786, hanzo-dev: 5,261)
- **40%+ LOC coverage** (14,874 commits with detailed LOC data)
- **~34M lines added**, ~21M deleted
- **~13M net LOC change**
- **894 unique repositories**
- **15 years** of coding history (2010-2025)

## Key Features
- **Multi-user support** with combined "All" view
- **SQL Console** - Query data with sql.js in the browser
- **GraphQL API** - Client-side GraphQL schema and resolvers
- **Tabbed interface** - Dashboard | SQL | GraphQL
- **Real LOC stats** from commit details (not simplified contribution counts)
- **SQLite database** for rich query support
- **GitHub Pages deployment** with embedded JSON data
- **Interactive Plotly visualizations**
- **Live LOC fetch progress** with streaming updates
- **SDK structure** - Easy to fork for other projects

## Query Interfaces

### SQL Console
```sql
-- Top repos by commits
SELECT repo, COUNT(*) as commits, SUM(additions) as added
FROM commits WHERE repo IS NOT NULL
GROUP BY repo ORDER BY commits DESC LIMIT 15;

-- Yearly breakdown
SELECT strftime('%Y', date) as year, COUNT(*) as commits
FROM commits GROUP BY year ORDER BY year DESC;
```

### GraphQL API
```graphql
{
  stats(username: "zeekay") {
    totalCommits
    totalAdditions
    totalDeletions
    netLocChange
    yearsCoding
    currentStreak
    longestStreak
  }
}
```

## Tech Stack
- Flask backend with SQLite storage
- sql.js for browser-side SQLite queries
- GraphQL schema with client-side resolvers
- Plotly.js for client-side charts
- GitHub Actions for static site deployment
- SSE (Server-Sent Events) for live progress updates

## Configuration (.env)
```bash
GITHUB_TOKEN=<your_token>
GITHUB_USERS=zeekay,hanzo-dev
PORT=5001
START_DATE=2010-01-01
DEBUG=True
```

## SDK Usage
```python
from hanzo_stats import GitHubStatsAnalyzer, StatsDB

# Initialize analyzer
analyzer = GitHubStatsAnalyzer()

# Get user stats
data = analyzer.get_user_data('zeekay')
print(data['stats']['total_commits'])
```

## Running Locally
```bash
# Setup
cp .env.example .env
# Edit .env with your GitHub token

# Run
source .venv/bin/activate
python app.py
# Open http://localhost:5001
```

## Static Export
```bash
python app.py --export docs
# Preview: python -m http.server -d docs 8000
```

## API Endpoints
- `GET /` - Dashboard UI with tabs (Dashboard, SQL, GraphQL)
- `GET /api/data` - All users data with combined view
- `GET /api/fetch-status` - LOC fetch progress
- `GET /api/fetch/<username>` - Fetch new user (SSE stream)
- `GET /api/fetch-loc-stream` - Fetch more LOC data (SSE stream)
- `GET /api/user/<username>` - Single user stats

## Database Schema (SQLite)
```sql
-- Users table with profile info
CREATE TABLE users (
    username TEXT PRIMARY KEY,
    avatar_url TEXT, name TEXT, bio TEXT,
    company TEXT, location TEXT, blog TEXT,
    followers INTEGER, following INTEGER,
    public_repos INTEGER, created_at TEXT
);

-- Commits table
CREATE TABLE commits (
    sha TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    date TEXT NOT NULL,
    repo TEXT, message TEXT, url TEXT,
    additions INTEGER, deletions INTEGER
);
```

## GraphQL Schema
```graphql
type User { username, name, avatarUrl, bio, followers, publicRepos }
type Commit { sha, username, date, repo, message, additions, deletions }
type Stats { totalCommits, totalAdditions, totalDeletions, netLocChange, yearsCoding, ... }
type Repo { repo, commits, additions, deletions }

type Query {
    user(username: String!): User
    users: [User]
    commits(username: String, repo: String, limit: Int): [Commit]
    stats(username: String!): Stats
    topRepos(username: String, limit: Int): [Repo]
}
```

## Files
- `app.py` - Flask app with SQLite backend
- `templates/index.html` - Dark-themed dashboard with SQL/GraphQL tabs
- `hanzo_stats/` - SDK package for reuse
- `cache/stats.db` - SQLite database (local only)
- `docs/` - Static export for GitHub Pages
- `.github/workflows/pages.yml` - GitHub Actions deployment
- `pyproject.toml` - Package configuration

## Forking for Other Projects
1. Fork this repo
2. Copy `.env.example` to `.env`
3. Edit `GITHUB_USERS` with your usernames
4. Run `python app.py` to fetch data
5. Export with `python app.py --export docs`
6. Enable GitHub Pages from `docs/` folder

## GitHub Pages Deployment
1. Push changes to `docs/` folder
2. GitHub Actions automatically deploys to Pages
3. Static site loads embedded JSON data client-side
4. SQL queries run in browser via sql.js (WebAssembly)
