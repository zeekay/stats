# GitHub Contribution Analyzer

## Overview
Flask web app that fetches **real GitHub contribution data** including actual lines of code (LOC) added/deleted since January 1, 2021.

## Key Features
- Real LOC stats from commit details (not simplified contribution counts)
- **Persistent caching** - commits stored in JSON files, survives restarts
- **Incremental fetching** - fetches data month-by-month, can resume
- Yearly breakdown of commits and LOC
- 30-day period comparisons with % change
- Interactive Plotly visualizations

## Configuration (.env)
```
GITHUB_TOKEN=<your_token>
GITHUB_USERS=zeekay
PORT=5001
START_DATE=2021-01-01
DEBUG=True
CACHE_DIR=./cache
```

## Running
```bash
source .venv/bin/activate
python app.py
# Open http://localhost:5001
```

## Architecture

### Persistent Caching System
The app uses a `CommitCache` class that stores commits in JSON files:
- `./cache/{username}_commits.json` - All commits with LOC data
- `./cache/{username}_meta.json` - Tracks which months have been fetched

### Data Flow
1. **Commit Search**: Uses GitHub Search API (`/search/commits`) to find commits by author
2. **Month-by-Month**: Fetches commits for each month since START_DATE
3. **Overflow Handling**: If a month has >1000 commits, splits into weeks/days
4. **LOC Fetching**: Incrementally fetches additions/deletions for each commit
5. **Aggregation**: Builds daily stats for visualization

### Rate Limiting
- 100ms delay between requests
- Automatically waits on 403 rate limit responses
- LOC data fetched in batches of 200-500 commits

## API Endpoints
- `GET /` - Dashboard UI
- `GET /api/data` - All users data
- `GET /api/user/<username>` - Single user stats
- `GET /api/refresh` - Reset cache metadata (refetch all months)
- `GET /api/fetch-more-loc` - Fetch more LOC stats for cached commits

## Stats Provided
- Total/average commits, additions, deletions, net LOC
- Yearly breakdown with commits, LOC, days active
- 30-day period comparisons
- Last 7/30 day summaries
- Growth rate analysis

## Files
- `app.py` - Flask app with CommitCache and GitHubContributionAnalyzer
- `templates/index.html` - Dark-themed dashboard with Plotly charts
- `cache/` - Persistent commit storage (JSON files)

## Notes
- Fetching all commits since 2021 takes time due to rate limits
- LOC data is fetched incrementally - run `/api/fetch-more-loc` to get more
- For users with many commits, initial fetch may take 30-60 minutes
