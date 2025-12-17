# GitHub Stats

Analyze your real GitHub contribution history - not just the green squares, but actual lines of code added and deleted across all your repositories.

![Dashboard Preview](docs/preview.png)

## Why This Tool?

GitHub's contribution graph only shows a count of contributions. This tool fetches the actual commit data to calculate:

- **Real lines of code** added and deleted
- **Commit frequency** over time  
- **Year-over-year growth** patterns
- **Activity heatmaps** by month

Perfect for:
- Portfolio documentation
- Performance reviews
- Understanding your coding patterns
- Tracking productivity over time

## Features

- Fetches commits via GitHub Search API (works across all repos you've contributed to)
- Persistent caching to avoid re-fetching data
- Multi-account support (track multiple GitHub usernames)
- Interactive web dashboard with Plotly visualizations
- **Static export for GitHub Pages** - host your stats for free
- Month-by-month incremental fetching with smart overflow handling

## Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/zeekay/stats.git
cd stats

# Create virtual environment (using uv)
uv venv
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
# Get a token at: https://github.com/settings/tokens
# Required scopes: repo (or public_repo for public repos only)
GITHUB_TOKEN=ghp_your_token_here

# Your GitHub username(s)
GITHUB_USERS=your_username

# How far back to analyze
START_DATE=2021-01-01
```

### 3. Run

```bash
python app.py
```

Open http://localhost:5001 to view your dashboard.

### 4. Export Static Site (for GitHub Pages)

```bash
python app.py --export ./docs
```

This generates a self-contained `index.html` in the `./docs` folder that you can host anywhere.

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` | (required) | GitHub Personal Access Token |
| `GITHUB_USERS` | (required) | Comma-separated list of usernames |
| `START_DATE` | `2021-01-01` | How far back to fetch data |
| `PORT` | `5001` | Web server port |
| `DEBUG` | `True` | Enable debug mode |
| `CACHE_DIR` | `./cache` | Where to store cached data |
| `REQUEST_DELAY` | `0.1` | Seconds between API requests |

## How It Works

### Data Collection

1. **Commit Search**: Uses GitHub's Search API to find all commits by author, month by month
2. **Overflow Handling**: Months with >1000 commits are automatically split into weeks/days
3. **LOC Fetching**: Individual commit details are fetched to get additions/deletions
4. **Caching**: All data is cached locally to avoid re-fetching

### Rate Limiting

GitHub API has rate limits:
- Search API: 30 requests/minute (unauthenticated) or 10 requests/minute (authenticated)
- REST API: 5000 requests/hour

The tool automatically:
- Waits when rate limited
- Saves progress incrementally
- Resumes where it left off

### Initial Fetch Time

First-time data collection takes time depending on your history:
- 1 year of data: ~5-10 minutes
- 5 years of data: ~30-60 minutes
- 10+ years: 1-2 hours

After initial fetch, subsequent runs are fast (only fetches new data).

## Publishing to GitHub Pages

1. Export your stats:
   ```bash
   python app.py --export ./docs
   ```

2. Commit and push:
   ```bash
   git add docs/
   git commit -m "Update stats dashboard"
   git push
   ```

3. Enable GitHub Pages:
   - Go to repo Settings > Pages
   - Source: Deploy from branch
   - Branch: `main` folder: `/docs`
   - Save

Your stats will be live at `https://username.github.io/stats/`

## API Endpoints (Web Server Mode)

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard UI |
| `GET /api/data` | All users' data and visualizations |
| `GET /api/user/<username>` | Single user's data |
| `GET /api/refresh` | Clear cache metadata and refetch |
| `GET /api/fetch-more-loc` | Fetch LOC data for cached commits |

## Development

```bash
# Run in debug mode
DEBUG=True python app.py

# Run tests
pytest

# Format code
black app.py
```

## Security

**Important**: Never commit your `.env` file or expose your GitHub token.

The `.gitignore` is configured to exclude:
- `.env` (contains your token)
- `cache/` (contains your data)

If you accidentally expose a token:
1. Immediately revoke it at https://github.com/settings/tokens
2. Generate a new token
3. Update your `.env` file

## Tech Stack

- **Python 3.10+**
- **Flask** - Web framework
- **Plotly** - Interactive charts
- **Pandas** - Data analysis
- **Requests** - HTTP client

## License

MIT License - See [LICENSE](LICENSE) file.

## Contributing

Contributions welcome! Please read the contributing guidelines first.

1. Fork the repo
2. Create a feature branch
3. Make your changes
4. Submit a PR

## Acknowledgments

Built with the GitHub API. Inspired by wanting to know actual productivity metrics beyond the contribution graph.
