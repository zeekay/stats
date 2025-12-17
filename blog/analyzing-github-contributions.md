# Beyond the Green Squares: Analyzing Your Real GitHub Contribution History

*How I built a tool to track actual lines of code written over 15 years of coding*

---

If you've ever looked at your GitHub contribution graph and wondered "but how much code did I actually write?", you're not alone. GitHub's green squares show activity, but they don't tell the real story.

I wanted to know:
- How many lines of code have I actually added and deleted?
- How has my productivity changed over the years?
- What do my coding patterns really look like?

So I built a tool to find out. Here's what I learned.

## The Problem with GitHub's Contribution Graph

GitHub's contribution graph counts "contributions" - commits, PRs, issues, and code reviews. But it treats a one-line typo fix the same as a 10,000-line feature implementation.

For context:
- A commit that fixes a typo: 1 contribution
- A commit that adds an entire authentication system: 1 contribution

Not very useful for understanding actual productivity.

## What I Built

I created [GitHub Stats](https://github.com/zeekay/stats) - a Python tool that:

1. **Fetches every commit** you've made across all repositories
2. **Gets the actual diff stats** - lines added and deleted per commit
3. **Caches everything locally** so you don't re-fetch data
4. **Visualizes the results** with interactive charts
5. **Exports a static dashboard** you can host on GitHub Pages

## The Technical Challenge

### Challenge 1: Finding All Commits

GitHub doesn't have an API endpoint for "give me all commits by this user across all repos." You have to either:
- List all repos you've contributed to, then fetch commits from each (slow, misses org repos)
- Use the Search API to find commits by author (has limits but works well)

I went with the Search API:

```python
query = f'author:{username} committer-date:{start_date}..{end_date}'
response = requests.get('https://api.github.com/search/commits', 
                       params={'q': query, 'per_page': 100})
```

### Challenge 2: The 1000 Result Limit

GitHub's Search API returns a maximum of 1000 results per query. For prolific developers, a single month might exceed this.

My solution: progressive time slicing.

```python
# First try the whole month
commits, hit_limit = search_commits_for_range(start, end)

if hit_limit:
    # Split into weeks
    for week in weeks_in_month:
        commits, hit_limit = search_commits_for_range(week_start, week_end)
        
        if hit_limit:
            # Split into individual days
            for day in days_in_week:
                commits = search_commits_for_range(day, day)
```

This handles even the most productive days.

### Challenge 3: Getting Line Counts

The search results don't include additions/deletions - you need to fetch each commit individually:

```python
# For each commit, fetch the full details
response = requests.get(commit['url'])
stats = response.json().get('stats', {})
additions = stats.get('additions', 0)
deletions = stats.get('deletions', 0)
```

With 30,000+ commits, this takes time. That's why caching is essential.

### Challenge 4: Rate Limiting

GitHub's API has limits:
- 5000 requests/hour for REST API
- 30 requests/minute for Search API

The tool handles this gracefully:

```python
if resp.status_code == 403:
    reset_time = int(resp.headers.get('X-RateLimit-Reset', 0))
    wait_time = max(reset_time - time.time(), 60)
    print(f"Rate limited, waiting {wait_time:.0f}s...")
    time.sleep(wait_time)
```

## My Results

After running the tool on my 15 years of GitHub history (2010-2025), here's what I found:

**Total commits:** 36,000+  
**Lines added:** 109+ million  
**Lines deleted:** 56+ million  
**Net LOC:** +53 million

The year-over-year breakdown was fascinating. I could see:
- When I started contributing to open source more heavily
- Major project launches as spikes in activity
- The shift from personal projects to professional codebases

## How to Use It Yourself

### 1. Clone and Setup

```bash
git clone https://github.com/zeekay/stats.git
cd stats

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```bash
GITHUB_TOKEN=ghp_your_token_here
GITHUB_USERS=your_username
START_DATE=2015-01-01  # How far back to analyze
```

Get a token at https://github.com/settings/tokens (needs `repo` scope).

### 3. Run the Dashboard

```bash
python app.py
```

Open http://localhost:5001 and wait for data to load. First run takes time depending on your history.

### 4. Export to GitHub Pages

```bash
python app.py --export ./docs
```

This creates a self-contained HTML file you can host anywhere.

Push to GitHub and enable Pages:
- Go to Settings > Pages
- Source: Deploy from branch
- Branch: main, folder: /docs

Your stats will be live at `https://username.github.io/stats/`

## What I Learned

### 1. Deletions Matter

I was surprised how much code I've deleted over the years. But that's a good thing - refactoring, removing dead code, and simplifying implementations are all valuable.

### 2. Consistency > Intensity

Looking at my patterns, consistent daily contributions beat occasional marathon sessions. The best years weren't the ones with massive single-day commits, but steady daily progress.

### 3. Multi-Account Reality

I use two GitHub accounts (personal and work). The tool supports multiple accounts, and seeing the combined view was eye-opening - my "work" account was far more active than I realized.

## Future Ideas

Some things I'd like to add:
- Language breakdown (how much Go vs Python vs TypeScript?)
- Repository categorization (open source vs private)
- Time-of-day patterns (when do I code most?)
- Collaboration metrics (commits to others' repos)

## Try It Out

The tool is open source: [github.com/zeekay/stats](https://github.com/zeekay/stats)

See my live dashboard: [zeekay.io/stats](https://zeekay.io/stats/)

If you try it out, I'd love to hear what patterns you discover in your own history. The green squares are just the beginning - the real story is in the lines of code.

---

*Built with Python, Flask, Plotly, and too much curiosity about my own commit history.*
