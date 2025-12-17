# Visualization helpers for GitHub Stats SDK

import pandas as pd
import plotly.graph_objs as go


def create_visualizations(data: dict) -> dict:
    """Create Plotly visualizations from stats data."""
    viz = {}
    dark_layout = {
        'template': 'plotly_dark',
        'paper_bgcolor': 'rgba(0,0,0,0)',
        'plot_bgcolor': 'rgba(0,0,0,0)',
        'font': {'color': '#fff'},
        'margin': {'l': 40, 'r': 20, 't': 20, 'b': 40},
        'xaxis': {'gridcolor': '#262626'},
        'yaxis': {'gridcolor': '#262626'}
    }

    # Daily commits timeline
    if data.get('daily'):
        df = pd.DataFrame(data['daily'])
        df['date'] = pd.to_datetime(df['date'])
        df['commits_7d'] = df['commits'].rolling(7, min_periods=1).mean()
        df['commits_30d'] = df['commits'].rolling(30, min_periods=1).mean()

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits'], mode='lines',
            name='Daily', line={'color': 'rgba(255,255,255,0.2)', 'width': 1}))
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits_7d'], mode='lines',
            name='7-day avg', line={'color': '#fff', 'width': 2}))
        fig.add_trace(go.Scatter(x=df['date'], y=df['commits_30d'], mode='lines',
            name='30-day avg', line={'color': 'rgba(255,255,255,0.5)', 'width': 2, 'dash': 'dash'}))
        fig.update_layout(**dark_layout, legend={'orientation': 'h', 'y': 1.1})
        viz['commits_timeline'] = fig.to_json()

    # LOC timeline
    if data.get('daily'):
        df = pd.DataFrame(data['daily'])
        df['date'] = pd.to_datetime(df['date'])
        df['net'] = df['additions'] - df['deletions']
        df['net_30d'] = df['net'].rolling(30, min_periods=1).mean()

        fig = go.Figure()
        fig.add_trace(go.Bar(x=df['date'], y=df['additions'], name='Added',
            marker_color='rgba(255,255,255,0.7)'))
        fig.add_trace(go.Bar(x=df['date'], y=-df['deletions'], name='Deleted',
            marker_color='rgba(255,255,255,0.3)'))
        fig.add_trace(go.Scatter(x=df['date'], y=df['net_30d'], mode='lines',
            name='Net (30d)', line={'color': '#fff', 'width': 3}))
        fig.update_layout(**dark_layout, barmode='relative', legend={'orientation': 'h', 'y': 1.1})
        viz['loc_timeline'] = fig.to_json()

    # Top repos
    if data.get('top_repos'):
        df = pd.DataFrame(data['top_repos'][:10])
        df['short_repo'] = df['repo'].apply(lambda x: x.split('/')[-1][:20] if x else '')

        fig = go.Figure()
        fig.add_trace(go.Bar(x=df['commits'], y=df['short_repo'], orientation='h',
            marker_color='#fff', text=df['commits'], textposition='auto'))
        fig.update_layout(**dark_layout, yaxis={'autorange': 'reversed'}, xaxis_title='Commits')
        viz['top_repos'] = fig.to_json()

    # Monthly heatmap
    if data.get('monthly'):
        df = pd.DataFrame(data['monthly'])
        pivot = df.pivot(index='year', columns='month', values='commits').fillna(0)

        fig = go.Figure(data=go.Heatmap(
            z=pivot.values, y=[str(y) for y in pivot.index],
            x=['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
            colorscale=[[0, '#0a0a0a'], [0.5, '#404040'], [1, '#ffffff']]
        ))
        fig.update_layout(**dark_layout)
        viz['heatmap'] = fig.to_json()

    # Yearly bar chart
    if data.get('yearly'):
        df = pd.DataFrame(data['yearly'])
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df['year'], y=df['commits'], name='Commits',
            marker_color='#fff', text=df['commits'], textposition='auto'))
        fig.update_layout(**dark_layout)
        viz['yearly_commits'] = fig.to_json()

    return viz
