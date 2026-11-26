"""
OptiCloud.ai — Dashboard (Premium + Full Agent Pipeline Integration)
────────────────────────────────────────────────────────────────────
Run :  python app.py           (from the dashboard/ folder)
       python dashboard/app.py (from the project root)
Open:  http://localhost:8050
Deps:  pip install dash pandas
"""

import sys
import os

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.abspath(os.path.join(_DASH_DIR, '..'))
for _p in (_DASH_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dash
from dash import html, dcc, Input, Output, State
import plotly.graph_objects as go
import pandas as pd

import db
import pages

try:
    from config.app import LocalMonitor, run_pipeline
    from Agents.agent_forecast import ForecastAgent
    _PIPELINE_AVAILABLE = True
except Exception:
    _PIPELINE_AVAILABLE = False
    LocalMonitor = None
    run_pipeline = None
    ForecastAgent = None


# ═══════════════════════════════════════════════════════════════════════════
#  App — with Inter font + Font Awesome CDN
# ═══════════════════════════════════════════════════════════════════════════

external_stylesheets = [
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css',
]

app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,
    title='OptiCloud.ai',
    update_title=None,
    external_stylesheets=external_stylesheets,
)

server = app.server  # for production WSGI deployment


# ── Chart defaults — matched to reference design ──────────────────────────

_L = dict(
    paper_bgcolor='rgba(0,0,0,0)',
    plot_bgcolor='rgba(0,0,0,0)',
    font=dict(
        color='#94a3b8', size=11,
        family="Inter, system-ui, -apple-system, sans-serif",
    ),
    margin=dict(l=46, r=14, t=28, b=36),
    xaxis=dict(gridcolor='rgba(148,163,184,.07)',
               linecolor='rgba(148,163,184,.16)', zeroline=False,
               tickfont=dict(color='#7c8ea6', size=11)),
    yaxis=dict(gridcolor='rgba(148,163,184,.07)',
               linecolor='rgba(148,163,184,.16)', zeroline=False,
               tickfont=dict(color='#7c8ea6', size=11)),
    hoverlabel=dict(bgcolor='#0c1524', bordercolor='rgba(56,189,248,.24)',
                    font_color='#e7eef8', font_size=12,
                    font_family='Inter, sans-serif'),
    legend=dict(font_size=11, bgcolor='rgba(0,0,0,0)',
                font_color='#94a3b8'),
)

# Colour palette matching the reference dashboard
CYA, GRN, PUR, ORG, RED, BLU = (
    '#38bdf8', '#35d49a', '#9b7cff', '#f6b94b', '#ff647c', '#4f7cff'
)


def _fig(**kw):
    """Blank figure with the OptiCloud dark theme."""
    return go.Figure(layout={**_L, **kw})


def _empty_fig(msg='No data available'):
    """Figure showing a centred placeholder message."""
    fig = _fig()
    fig.add_annotation(
        text=msg, xref='paper', yref='paper', x=.5, y=.5,
        showarrow=False, font=dict(color='#7c8ea6', size=14),
    )
    return fig


def _area(fig, df, col, color=CYA, name=None):
    """Add a smooth area trace to *fig*."""
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    fig.add_trace(go.Scatter(
        x=df['timestamp'], y=df[col], name=name or col,
        mode='lines', line=dict(color=color, width=2, shape='spline'),
        fill='tozeroy', fillcolor=f'rgba({r},{g},{b},.08)',
        hovertemplate='%{y:.1f}<extra></extra>',
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  Layout — Premium sidebar + topbar + page content
# ═══════════════════════════════════════════════════════════════════════════

_db_ok = db.db_exists()

sidebar = html.Div(className='sidebar', children=[
    # ── Brand mark ────────────────────────────────────────────────────
    html.Div(className='sidebar-brand', children=[
        html.Div(className='brand-mark', children=[
            html.I(className='fa-solid fa-bolt'),
        ]),
        html.Div(className='brand-copy', children=[
            html.Strong('OptiCloud'),
            html.Span('Cloud Optimizer'),
        ]),
    ]),

    # ── Navigation ────────────────────────────────────────────────────
    html.P('Workspace', className='nav-label'),
    html.Nav([
        dcc.Link([
            html.I(className='fa-solid fa-table-cells-large'),
            html.Span(' Overview'),
        ], href='/overview', className='nav-link'),

        dcc.Link([
            html.I(className='fa-solid fa-chart-line'),
            html.Span(' Metrics'),
        ], href='/metrics', className='nav-link'),

        dcc.Link([
            html.I(className='fa-solid fa-chart-area'),
            html.Span(' Forecasts'),
        ], href='/forecasts', className='nav-link'),

        dcc.Link([
            html.I(className='fa-solid fa-code-branch'),
            html.Span(' Decisions'),
        ], href='/decisions', className='nav-link'),
    ]),

    # ── Status panel ──────────────────────────────────────────────────
    html.Div(className='sidebar-status', children=[
        html.Div(id='sb-status-dot',
                 className=f'status-dot{" " if _db_ok else " off"}'),
        html.Div([
            html.Strong(id='sb-status-title',
                        children='All systems operational' if _db_ok else 'No database'),
            html.Span(id='sb-status-sub',
                      children='Pipeline connected' if _db_ok else 'Waiting for metrics.db'),
        ]),
    ]),
])

topbar = html.Div(className='topbar', children=[
    html.Div(className='topbar-left', children=[
        html.Div([
            html.H1(id='page-title', children='Dashboard'),
            html.Div(className='breadcrumb', children=[
                'OptiCloud.ai',
                html.Span('/'),
                html.Span(id='breadcrumb-text', children='Overview'),
            ]),
        ]),
    ]),
    html.Div(className='topbar-right', children=[
        html.Div(className='env-badge', children=[
            html.I(className='fa-solid fa-layer-group'),
            html.Span(id='env-badge-text', children='Dry-Run Mode'),
        ]),
    ]),
])

app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    dcc.Store(id='_'),
    dcc.Store(id='pipeline-trigger', data=0),
    sidebar,
    html.Div(className='main-content', children=[
        topbar,
        html.Div(id='page'),
    ]),
])


# ═══════════════════════════════════════════════════════════════════════════
#  Routing & Global Status
# ═══════════════════════════════════════════════════════════════════════════

_PAGES = {
    '/':          pages.overview,
    '/overview':  pages.overview,
    '/metrics':   pages.metrics_page,
    '/forecasts': pages.forecasts,
    '/decisions': pages.decisions_page,
}

_TITLES = {
    '/':          ('Dashboard',  'Overview'),
    '/overview':  ('Dashboard',  'Overview'),
    '/metrics':   ('Metrics',    'Metrics'),
    '/forecasts': ('Forecasts',  'Forecasts'),
    '/decisions': ('Decisions',  'Decisions'),
}


@app.callback(
    Output('page', 'children'),
    Output('page-title', 'children'),
    Output('breadcrumb-text', 'children'),
    Input('url', 'pathname'),
)
def route(path):
    page_fn = _PAGES.get(path, pages.overview)
    t, b = _TITLES.get(path, ('Dashboard', 'Overview'))
    return page_fn(), t, b


@app.callback(
    Output('sb-status-dot', 'className'),
    Output('sb-status-title', 'children'),
    Output('sb-status-sub', 'children'),
    Output('env-badge-text', 'children'),
    Input('url', 'pathname'),
    Input('pipeline-trigger', 'data'),
)
def update_global_status(_path, _trigger):
    ok = db.db_exists()
    s = db.summary() if ok else {'dry_run': True, 'metrics': 0}

    dot_cls = 'status-dot' if ok else 'status-dot off'
    st_title = 'All systems operational' if ok else 'No database'
    st_sub = f'{s["metrics"]} metric rows loaded' if ok else 'Waiting for metrics.db'
    env_txt = 'Dry-Run Mode' if s.get('dry_run', True) else 'Live Azure Mode'
    return dot_cls, st_title, st_sub, env_txt


# Sidebar: highlight the active link
app.clientside_callback(
    """function(p) {
        document.querySelectorAll('.nav-link').forEach(function(a) {
            var h = a.getAttribute('href');
            var on = (h === p) || (p === '/' && h === '/overview');
            if (on) a.classList.add('active');
            else    a.classList.remove('active');
        });
        return '';
    }""",
    Output('_', 'data'),
    Input('url', 'pathname'),
)


# ═══════════════════════════════════════════════════════════════════════════
#  Pipeline Runner (Overview Hero Button)
# ═══════════════════════════════════════════════════════════════════════════

@app.callback(
    Output('pipeline-status', 'children'),
    Output('pipeline-trigger', 'data'),
    Input('run-pipeline-btn', 'n_clicks'),
    State('pipeline-trigger', 'data'),
    prevent_initial_call=True,
)
def trigger_pipeline(n_clicks, current):
    if not n_clicks:
        return '', current or 0
    if not _PIPELINE_AVAILABLE or run_pipeline is None:
        return html.Span(
            'Pipeline agents unavailable (check dependencies).',
            className='status-pill err',
        ), current or 0

    try:
        res = run_pipeline(dry_run=True, db_path=db._DB)
        if not res.get('ok'):
            return html.Span(
                res.get('error', 'Pipeline did not complete.'),
                className='status-pill warn',
            ), current or 0

        dec = res.get('decision', {})
        dep = res.get('deployment', {})
        action = str(dec.get('action', 'hold')).upper()
        dep_status = dep.get('status', 'logged')
        pill = html.Span([
            html.I(className='fa-solid fa-check'),
            f'{action} · Deploy: {dep_status} · Explanation saved',
        ], className='status-pill ok')
        return pill, (current or 0) + 1
    except Exception as exc:
        return html.Span(
            f'Pipeline error: {exc}',
            className='status-pill err',
        ), current or 0


# ═══════════════════════════════════════════════════════════════════════════
#  Overview
# ═══════════════════════════════════════════════════════════════════════════

@app.callback(
    Output('k-sav', 'children'),   Output('k-sav-s', 'children'),
    Output('k-dec', 'children'),   Output('k-dec-s', 'children'),
    Output('k-dep', 'children'),   Output('k-dep-s', 'children'),
    Output('k-cpu', 'children'),   Output('k-cpu-bar', 'children'),
    Output('hs-dec', 'children'),  Output('hs-dep', 'children'),
    Output('hs-sav', 'children'),  Output('hs-cpu', 'children'),
    Output('ov-cpu', 'figure'),
    Output('ov-donut', 'figure'),
    Output('ov-table', 'children'),
    Input('ov-tick', 'n_intervals'),
    Input('pipeline-trigger', 'data'),
)
def update_overview(_, _trigger):
    s = db.summary()

    # ── KPIs ──────────────────────────────────────────────────────────
    sav   = f'₹{s["savings"]:,.0f}'
    sav_s = html.Span([
        html.I(className='fa-solid fa-arrow-trend-up',
               style={'marginRight': '4px'}),
        f'from {s["down"]} optimisation{"s" if s["down"] != 1 else ""}',
    ], className='positive')

    dec   = str(s['decisions'])
    dec_s = html.Span(f'{s["down"]} down · {s["up"]} up · {s["hold"]} hold')

    dep   = str(s['deploys'])
    dep_ok = s['ok']
    dep_fail = s['fail']
    dep_s = html.Span([
        html.Span(f'{dep_ok} ok', className='positive'),
        f' · {dep_fail} failed',
    ])

    avg_cpu = s['avg_cpu']
    cpu_text = f'{avg_cpu}%'
    cpu_bar = html.Div(className='action-meter', children=[
        html.I(style={'width': f'{min(avg_cpu, 100)}%'}),
    ])

    # ── Hero stats ────────────────────────────────────────────────────
    hs_dec = str(s['decisions'])
    hs_dep = str(s['deploys'])
    hs_sav = f'₹{s["savings"]:,.0f}'
    hs_cpu = f'{avg_cpu}%'

    # ── CPU chart ─────────────────────────────────────────────────────
    df = db.metrics(24)
    if df.empty:
        cpu_fig = _empty_fig()
    else:
        cpu_fig = _fig(showlegend=False,
                       yaxis_title='%', yaxis_range=[0, 100])
        _area(cpu_fig, df, 'cpu_percent', CYA)

    # ── Donut ─────────────────────────────────────────────────────────
    vals = [s['down'], s['up'], s['hold']]
    if any(vals):
        total = sum(vals)
        donut = go.Figure(go.Pie(
            values=vals,
            labels=['Downsize', 'Upsize', 'Hold'],
            marker=dict(colors=[GRN, ORG, PUR],
                        line=dict(color='rgba(7,11,19,.8)', width=2)),
            hole=.65, textinfo='label+value',
            textfont=dict(size=11, color='#94a3b8',
                          family='Inter, sans-serif'),
            hovertemplate='%{label}: %{value}<extra></extra>',
        ))
        donut.update_layout(**_L, showlegend=False)
        donut.add_annotation(
            text=f'<b>{total}</b>', x=.5, y=.5,
            font=dict(size=22, color='#e7eef8',
                      family='Inter, sans-serif'),
            showarrow=False,
        )
    else:
        donut = _empty_fig('No decisions yet')

    # ── Recent decisions table ────────────────────────────────────────
    ddf = db.decisions(5)
    if ddf.empty:
        tbl = html.Div(className='empty-state', children=[
            html.P('No decisions yet', style={'color': '#7c8ea6'}),
        ])
    else:
        hdr = html.Thead(html.Tr([
            html.Th(c) for c in ['Time', 'Action', 'From', '', 'To', 'Savings']
        ]))
        rows = []
        for _, r in ddf.iterrows():
            ac  = r['action']
            clr = GRN if ac == 'downsize' else ORG if ac == 'upsize' else PUR
            sv  = r.get('estimated_monthly_savings', 0) or 0
            rows.append(html.Tr([
                html.Td(r['timestamp'].strftime('%b %d, %H:%M')),
                html.Td(ac, style={'color': clr, 'fontWeight': '600'}),
                html.Td(r.get('current_size', '—') or '—'),
                html.Td('→', style={'color': '#7c8ea6', 'textAlign': 'center'}),
                html.Td(r.get('recommended_size', '—') or '—'),
                html.Td(f'₹{sv:,.0f}' if sv else '—',
                        style={'color': GRN} if sv else {}),
            ]))
        tbl = html.Table(className='data-table',
                         children=[hdr, html.Tbody(rows)])

    return (sav, sav_s, dec, dec_s, dep, dep_s, cpu_text, cpu_bar,
            hs_dec, hs_dep, hs_sav, hs_cpu,
            cpu_fig, donut, tbl)


# ═══════════════════════════════════════════════════════════════════════════
#  Metrics
# ═══════════════════════════════════════════════════════════════════════════

@app.callback(
    Output('m-cpu',  'figure'), Output('m-mem',  'figure'),
    Output('m-disk', 'figure'), Output('m-net', 'figure'),
    Input('m-range', 'value'),
)
def update_metrics(hours):
    df = db.metrics(hours)
    if df.empty:
        e = _empty_fig()
        return e, e, e, e

    # CPU
    f1 = _fig(showlegend=False, yaxis_title='%', yaxis_range=[0, 100])
    _area(f1, df, 'cpu_percent', CYA)

    # Memory (bytes → GB)
    mem = df.copy()
    mem['mem_gb'] = mem['available_memory'].fillna(0) / (1024 ** 3)
    f2 = _fig(showlegend=False, yaxis_title='GB')
    _area(f2, mem, 'mem_gb', color=PUR)

    # Disk I/O
    f3 = _fig(yaxis_title='ops/s')
    f3.add_trace(go.Scatter(
        x=df['timestamp'], y=df['disk_read_ops'], name='Read',
        mode='lines', line=dict(color=CYA, width=2, shape='spline'),
        hovertemplate='Read: %{y:.0f}<extra></extra>',
    ))
    f3.add_trace(go.Scatter(
        x=df['timestamp'], y=df['disk_write_ops'], name='Write',
        mode='lines', line=dict(color=ORG, width=2, shape='spline'),
        hovertemplate='Write: %{y:.0f}<extra></extra>',
    ))

    # Network (bytes → MB)
    net = df.copy()
    net['in_mb']  = net['network_in_bytes'].fillna(0) / (1024 ** 2)
    net['out_mb'] = net['network_out_bytes'].fillna(0) / (1024 ** 2)
    f4 = _fig(yaxis_title='MB')
    f4.add_trace(go.Scatter(
        x=net['timestamp'], y=net['in_mb'], name='In',
        mode='lines', line=dict(color=GRN, width=2, shape='spline'),
        hovertemplate='In: %{y:.1f} MB<extra></extra>',
    ))
    f4.add_trace(go.Scatter(
        x=net['timestamp'], y=net['out_mb'], name='Out',
        mode='lines', line=dict(color=RED, width=2, shape='spline'),
        hovertemplate='Out: %{y:.1f} MB<extra></extra>',
    ))

    return f1, f2, f3, f4


# ═══════════════════════════════════════════════════════════════════════════
#  Forecasts (Prophet ML via ForecastAgent, with rolling-avg fallback)
# ═══════════════════════════════════════════════════════════════════════════

def _scale_factor(metric: str) -> float:
    if metric == 'available_memory':
        return 1.0 / (1024 ** 3)
    if 'network' in metric:
        return 1.0 / (1024 ** 2)
    return 1.0


@app.callback(
    Output('f-chart', 'figure'),
    Output('f-avg', 'children'), Output('f-min', 'children'),
    Output('f-max', 'children'), Output('f-std', 'children'),
    Output('f-badge', 'children'),
    Input('f-metric', 'value'),
)
def update_forecast(metric):
    fallback_badge = [
        html.I(className='fa-solid fa-chart-line', style={'marginRight': '5px'}),
        'Rolling Avg',
    ]

    df = db.metrics(0)
    if df.empty or metric not in df.columns:
        return _empty_fig(), '—', '—', '—', '—', fallback_badge

    series = df[metric].dropna()
    if series.empty:
        return _empty_fig(), '—', '—', '—', '—', fallback_badge

    scale = _scale_factor(metric)
    vals = series * scale
    avg = f'{vals.mean():.1f}'
    mn  = f'{vals.min():.1f}'
    mx  = f'{vals.max():.1f}'
    std = f'{vals.std():.1f}'

    # Try real ForecastAgent (Prophet)
    if _PIPELINE_AVAILABLE and LocalMonitor is not None and ForecastAgent is not None:
        try:
            monitor = LocalMonitor(db_path=db._DB)
            forecaster = ForecastAgent(monitor, lookback_hours=48)
            fc_res = forecaster.get_forecast(metric, horizon_hours=6)
            fc = fc_res.get('forecast', pd.DataFrame())
            hist = fc_res.get('history', pd.DataFrame())

            if not fc.empty and not hist.empty:
                anom_res = forecaster.detect_anomalies(metric, lookback_hours=48)
                anomalies = anom_res.get('anomalies', pd.DataFrame())
                anom_cnt = anom_res.get('anomaly_count', 0)

                fig = _fig(showlegend=True)

                # 95% confidence band
                fig.add_trace(go.Scatter(
                    x=fc['ds'], y=fc['yhat_upper'] * scale,
                    mode='lines', line=dict(width=0),
                    showlegend=False, hoverinfo='skip',
                ))
                fig.add_trace(go.Scatter(
                    x=fc['ds'], y=fc['yhat_lower'] * scale,
                    name='95% Confidence',
                    mode='lines', line=dict(width=0),
                    fill='tonexty', fillcolor='rgba(56,189,248,.10)',
                    hoverinfo='skip',
                ))

                # Actual history
                fig.add_trace(go.Scatter(
                    x=hist['ds'], y=hist['y'] * scale, name='Actual',
                    mode='lines', line=dict(color='#94a3b8', width=1.2),
                    opacity=.65, hovertemplate='%{y:.1f}<extra></extra>',
                ))

                # Prophet forecast line
                fig.add_trace(go.Scatter(
                    x=fc['ds'], y=fc['yhat'] * scale, name='Prophet Forecast',
                    mode='lines', line=dict(color=CYA, width=2.4, shape='spline'),
                    hovertemplate='Forecast: %{y:.1f}<extra></extra>',
                ))

                # Anomalies
                if not anomalies.empty:
                    fig.add_trace(go.Scatter(
                        x=anomalies['ds'], y=anomalies['y'] * scale,
                        name=f'Anomalies ({anom_cnt})',
                        mode='markers',
                        marker=dict(color=RED, size=6, symbol='circle'),
                        hovertemplate='Anomaly: %{y:.1f}<extra></extra>',
                    ))

                badge = [
                    html.I(className='fa-solid fa-brain',
                           style={'marginRight': '5px'}),
                    f'Prophet ML · {anom_cnt} anomalies (48 h)',
                ]
                return fig, avg, mn, mx, std, badge
        except Exception:
            pass

    # Fallback: historical + 1h rolling mean
    plot = df.loc[series.index].copy()
    plot['value'] = vals.values
    plot['rolling'] = plot['value'].rolling(12, min_periods=1).mean()

    fig = _fig(showlegend=True)
    fig.add_trace(go.Scatter(
        x=plot['timestamp'], y=plot['value'], name='Actual',
        mode='lines', line=dict(color=CYA, width=1), opacity=.35,
        hovertemplate='%{y:.1f}<extra></extra>',
    ))
    fig.add_trace(go.Scatter(
        x=plot['timestamp'], y=plot['rolling'], name='Trend (1 h avg)',
        mode='lines', line=dict(color=CYA, width=2.5, shape='spline'),
        hovertemplate='%{y:.1f}<extra></extra>',
    ))

    return fig, avg, mn, mx, std, fallback_badge


# ═══════════════════════════════════════════════════════════════════════════
#  Decisions
# ═══════════════════════════════════════════════════════════════════════════

@app.callback(
    Output('d-table', 'data'),
    Input('d-filter', 'value'),
    Input('pipeline-trigger', 'data'),
)
def update_decisions(action_filter, _trigger):
    df = db.decisions()
    if df.empty:
        return []
    if action_filter != 'all':
        df = df[df['action'] == action_filter]

    df['time_fmt'] = df['timestamp'].dt.strftime('%b %d, %H:%M')
    sav = df['estimated_monthly_savings'].fillna(0)
    df['savings_fmt'] = sav.apply(lambda x: f'₹{x:,.0f}' if x else '—')

    return df.to_dict('records')


@app.callback(
    Output('d-detail',  'style'),
    Output('d-reason',  'children'),
    Output('d-explain', 'children'),
    Output('d-meta',    'children'),
    Input('d-table', 'selected_rows'),
    State('d-table', 'data'),
)
def show_explanation(sel, data):
    hide = {'marginTop': '16px', 'display': 'none'}
    if not sel or not data:
        return hide, '', '', ''

    row    = data[sel[0]]
    reason = row.get('reason', '') or ''
    did    = row.get('id')

    exp = db.explanation(did) if did else None
    if exp:
        text = exp['text']
        cost_part = f'  ·  LLM cost: ${exp["cost_usd"]:.5f}' if exp.get('cost_usd') else ''
        meta = html.Span([
            html.I(className='fa-solid fa-robot',
                   style={'marginRight': '5px'}),
            f'{exp["model"]}  ·  {exp["mode"]} mode{cost_part}',
        ])
    else:
        text = 'No AI explanation available for this decision.'
        meta = ''

    show = {'marginTop': '16px', 'display': 'block'}
    return show, reason, text, meta


# ═══════════════════════════════════════════════════════════════════════════
#  Run
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    app.run(debug=True, port=8050)
