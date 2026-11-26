"""
OptiCloud.ai — Page Layouts (Premium)

Each public function returns a Dash component tree for one dashboard page.
Design language matched to the reference HTML dashboard.
"""

from dash import html, dcc, dash_table


# ── Helpers ────────────────────────────────────────────────────────────────

def _kpi(label, vid, sid=None, icon=None, foot_id=None):
    """Glassmorphism KPI card with icon header, large value, optional footer."""
    head = html.Div(className='kpi-head', children=[
        html.Span(label),
        html.I(className=icon) if icon else html.Span(),
    ])
    ch = [
        head,
        html.Div(id=vid, className='kpi-value', children='—'),
    ]
    if sid:
        ch.append(html.Div(id=sid, className='kpi-foot'))
    if foot_id:
        ch.append(html.Div(id=foot_id))
    return html.Div(className='kpi-card', children=ch)


def _panel(title, children, subtitle=None, right=None):
    """Glassmorphism panel with panel-head pattern."""
    left = [html.H3(title)]
    if subtitle:
        left.append(html.P(subtitle))
    head_children = [html.Div(left)]
    if right:
        head_children.append(right)
    return html.Div(className='panel', children=[
        html.Div(className='panel-head', children=head_children),
        *children,
    ])


def _chart(title, gid, h=280, subtitle=None, right=None):
    """Panel wrapping a dcc.Graph."""
    left = [html.H3(title)]
    if subtitle:
        left.append(html.P(subtitle))
    head_children = [html.Div(left)]
    if right:
        head_children.append(right)
    return html.Div(className='panel', children=[
        html.Div(className='panel-head', children=head_children),
        dcc.Graph(id=gid, config={'displayModeBar': False},
                  style={'height': f'{h}px'}),
    ])


# ═══════════════════════════════════════════════════════════════════════════
#  Overview
# ═══════════════════════════════════════════════════════════════════════════

def overview():
    return html.Div(className='page-wrap', children=[
        dcc.Interval(id='ov-tick', interval=30_000, n_intervals=0),

        # ── AI Hero Banner ─────────────────────────────────────────────
        html.Div(className='ai-hero', children=[
            html.Div(className='hero-glow'),
            html.Div(className='ai-orb', children=[
                html.Div(className='orb-core', children=[
                    html.I(className='fa-solid fa-brain'),
                ]),
            ]),
            html.Div(className='hero-copy', children=[
                html.Div(className='eyebrow', children=[
                    html.Span(className='live-dot'),
                    ' AUTONOMOUS MODE ACTIVE',
                ]),
                html.H2('AI Optimization Engine'),
                html.P('Continuously analyzing VM workloads, cost patterns '
                       'and performance metrics for autonomous rightsizing.'),
                html.Div(className='hero-stats', id='hero-stats', children=[
                    html.Div([
                        html.Span('Decisions made'),
                        html.Strong('—', id='hs-dec'),
                    ]),
                    html.Div([
                        html.Span('Deployments'),
                        html.Strong('—', id='hs-dep'),
                    ]),
                    html.Div([
                        html.Span('Est. monthly savings'),
                        html.Strong('—', id='hs-sav'),
                    ]),
                    html.Div([
                        html.Span('Avg CPU (6 h)'),
                        html.Strong('—', id='hs-cpu'),
                    ]),
                ]),
            ]),
            html.Div(className='hero-actions', children=[
                html.Button([
                    html.I(className='fa-solid fa-play'),
                    'Run AI Pipeline',
                ], id='run-pipeline-btn', className='primary-btn', n_clicks=0),
                dcc.Loading(
                    type='dot', color='#38bdf8',
                    children=html.Div(id='pipeline-status', className='pipeline-status'),
                ),
            ]),
        ]),

        # ── KPI Cards ─────────────────────────────────────────────────
        html.Div(className='kpi-grid', children=[
            _kpi('Est. Savings / mo', 'k-sav', 'k-sav-s',
                 icon='fa-solid fa-piggy-bank'),
            _kpi('Decisions', 'k-dec', 'k-dec-s',
                 icon='fa-solid fa-code-branch'),
            _kpi('Deployments', 'k-dep', 'k-dep-s',
                 icon='fa-solid fa-rocket'),
            _kpi('Avg CPU (6 h)', 'k-cpu', foot_id='k-cpu-bar',
                 icon='fa-solid fa-microchip'),
        ]),

        # ── Charts Row ────────────────────────────────────────────────
        html.Div(className='content-grid wide', children=[
            _chart('CPU — Last 24 h', 'ov-cpu',
                   subtitle='Real-time VM processor utilisation'),
            _chart('Decision Breakdown', 'ov-donut',
                   subtitle='Distribution of optimisation actions'),
        ]),

        # ── Recent Decisions Table ────────────────────────────────────
        html.Div(className='panel', style={'marginTop': '16px'}, children=[
            html.Div(className='panel-head', children=[
                html.Div([
                    html.H3([
                        html.I(className='fa-solid fa-bolt accent-icon'),
                        ' Recent Decisions',
                    ]),
                    html.P('Latest actions taken by the optimizer'),
                ]),
            ]),
            html.Div(id='ov-table'),
        ]),
    ])


# ═══════════════════════════════════════════════════════════════════════════
#  Metrics
# ═══════════════════════════════════════════════════════════════════════════

def metrics_page():
    return html.Div(className='page-wrap', children=[
        html.Div(className='section-heading', children=[
            html.Div([
                html.Span('OBSERVABILITY', className='eyebrow'),
                html.H2('Metrics'),
                html.P('VM resource utilisation signals across your infrastructure.'),
            ]),
        ]),

        html.Div(className='filter-bar', children=[
            html.Span('Time range', className='filter-label'),
            dcc.Dropdown(
                id='m-range',
                options=[
                    {'label': '6 hours',  'value': 6},
                    {'label': '24 hours', 'value': 24},
                    {'label': '7 days',   'value': 168},
                    {'label': '30 days',  'value': 720},
                    {'label': 'All',      'value': 0},
                ],
                value=24, clearable=False,
                className='dark-dd', style={'width': '160px'},
            ),
        ]),

        html.Div(className='content-grid', children=[
            _chart('CPU Utilisation', 'm-cpu',
                   subtitle='Processor load percentage'),
            _chart('Available Memory', 'm-mem',
                   subtitle='Free memory in GB'),
        ]),
        html.Div(className='content-grid',
                 style={'marginTop': '16px'}, children=[
            _chart('Disk I/O', 'm-disk',
                   subtitle='Read/write operations per second'),
            _chart('Network Traffic', 'm-net',
                   subtitle='Inbound/outbound throughput in MB'),
        ]),
    ])


# ═══════════════════════════════════════════════════════════════════════════
#  Forecasts
# ═══════════════════════════════════════════════════════════════════════════

def forecasts():
    return html.Div(className='page-wrap', children=[
        html.Div(className='section-heading', children=[
            html.Div([
                html.Span('ANALYTICS', className='eyebrow'),
                html.H2('Forecasts'),
                html.P('Historical trends with rolling average projections.'),
            ]),
        ]),

        html.Div(className='filter-bar', children=[
            html.Span('Metric', className='filter-label'),
            dcc.Dropdown(
                id='f-metric',
                options=[
                    {'label': 'CPU %',          'value': 'cpu_percent'},
                    {'label': 'Memory (GB)',     'value': 'available_memory'},
                    {'label': 'Disk Read Ops',   'value': 'disk_read_ops'},
                    {'label': 'Disk Write Ops',  'value': 'disk_write_ops'},
                    {'label': 'Network In',      'value': 'network_in_bytes'},
                    {'label': 'Network Out',     'value': 'network_out_bytes'},
                ],
                value='cpu_percent', clearable=False,
                className='dark-dd', style={'width': '200px'},
            ),
            html.Span(id='f-badge', className='confidence-badge', children=[
                html.I(className='fa-solid fa-chart-line',
                       style={'marginRight': '5px'}),
                'Prophet ML Forecast',
            ]),
        ]),

        _chart('Trend & Prophet Forecast', 'f-chart', h=360,
               subtitle='Actual metrics, Prophet ML forecast & 95% confidence band'),

        # ── Forecast Stats ────────────────────────────────────────────
        html.Div(className='forecast-stats', style={'marginTop': '16px'},
                 children=[
            html.Div(className='forecast-stat', children=[
                html.Span('Average'),
                html.Strong('—', id='f-avg'),
            ]),
            html.Div(className='forecast-stat', children=[
                html.Span('Minimum'),
                html.Strong('—', id='f-min'),
            ]),
            html.Div(className='forecast-stat', children=[
                html.Span('Maximum'),
                html.Strong('—', id='f-max'),
            ]),
            html.Div(className='forecast-stat', children=[
                html.Span('Std Deviation'),
                html.Strong('—', id='f-std'),
            ]),
        ]),
    ])


# ═══════════════════════════════════════════════════════════════════════════
#  Decisions
# ═══════════════════════════════════════════════════════════════════════════

_COLS = [
    {'name': 'Time',       'id': 'time_fmt'},
    {'name': 'Action',     'id': 'action'},
    {'name': 'From',       'id': 'current_size'},
    {'name': 'To',         'id': 'recommended_size'},
    {'name': 'Confidence', 'id': 'confidence'},
    {'name': 'Risk',       'id': 'risk_level'},
    {'name': 'Savings/mo', 'id': 'savings_fmt'},
    {'name': 'Status',     'id': 'status'},
]

_CELL = dict(
    backgroundColor='transparent', color='#cbd5e1', fontSize='12.5px',
    border='none', borderBottom='1px solid rgba(148,163,184,.08)',
    padding='12px 14px', textAlign='left',
    fontFamily="'Inter', system-ui, sans-serif",
)

_HDR = dict(
    backgroundColor='rgba(10,18,31,.65)', color='#8fa0b7',
    fontWeight='600', fontSize='11px', textTransform='uppercase',
    letterSpacing='.06em',
    border='none', borderBottom='1px solid rgba(148,163,184,.15)',
    padding='12px 14px',
    fontFamily="'Inter', system-ui, sans-serif",
)

_COND = [
    {'if': {'state': 'selected'},
     'backgroundColor': 'rgba(56,189,248,.06)', 'border': 'none'},
    {'if': {'state': 'active'},
     'backgroundColor': 'rgba(56,189,248,.04)', 'border': 'none'},
    # Action colours
    {'if': {'filter_query': '{action} = "downsize"', 'column_id': 'action'},
     'color': '#35d49a'},
    {'if': {'filter_query': '{action} = "upsize"', 'column_id': 'action'},
     'color': '#f6b94b'},
    {'if': {'filter_query': '{action} = "hold"', 'column_id': 'action'},
     'color': '#9b7cff'},
    # Risk colours
    {'if': {'filter_query': '{risk_level} = "LOW"', 'column_id': 'risk_level'},
     'color': '#35d49a'},
    {'if': {'filter_query': '{risk_level} = "MEDIUM"', 'column_id': 'risk_level'},
     'color': '#f6b94b'},
    {'if': {'filter_query': '{risk_level} = "HIGH"', 'column_id': 'risk_level'},
     'color': '#ff647c'},
]


def decisions_page():
    return html.Div(className='page-wrap', children=[
        html.Div(className='section-heading', children=[
            html.Div([
                html.Span('AUTOMATION', className='eyebrow'),
                html.H2('Decisions'),
                html.P('Optimisation history and AI explanations.'),
            ]),
        ]),

        html.Div(className='filter-bar', children=[
            html.Span('Filter', className='filter-label'),
            dcc.Dropdown(
                id='d-filter',
                options=[
                    {'label': 'All',      'value': 'all'},
                    {'label': 'Downsize', 'value': 'downsize'},
                    {'label': 'Upsize',   'value': 'upsize'},
                    {'label': 'Hold',     'value': 'hold'},
                ],
                value='all', clearable=False,
                className='dark-dd', style={'width': '140px'},
            ),
        ]),

        # ── Pipeline Visualization ────────────────────────────────────
        html.Div(className='panel', style={'marginBottom': '16px'}, children=[
            html.Div(className='panel-head', children=[
                html.Div([
                    html.H3([
                        html.I(className='fa-solid fa-wand-magic-sparkles accent-icon'),
                        ' Decision Pipeline',
                    ]),
                    html.P('How OptiCloud.ai evaluates opportunities'),
                ]),
            ]),
            html.Div(className='pipeline', children=[
                html.Div(className='pipeline-step', children=[
                    html.Span(className='pipeline-icon', children=[
                        html.I(className='fa-solid fa-magnifying-glass-chart'),
                    ]),
                    html.Div([
                        html.Strong('Detect'),
                        html.Small('Identify anomalies & waste'),
                    ]),
                ]),
                html.Div(className='pipeline-line'),
                html.Div(className='pipeline-step', children=[
                    html.Span(className='pipeline-icon', children=[
                        html.I(className='fa-solid fa-brain'),
                    ]),
                    html.Div([
                        html.Strong('Predict'),
                        html.Small('Model cost / performance impact'),
                    ]),
                ]),
                html.Div(className='pipeline-line'),
                html.Div(className='pipeline-step', children=[
                    html.Span(className='pipeline-icon', children=[
                        html.I(className='fa-solid fa-code-branch'),
                    ]),
                    html.Div([
                        html.Strong('Decide'),
                        html.Small('Rank safe autonomous actions'),
                    ]),
                ]),
                html.Div(className='pipeline-line'),
                html.Div(className='pipeline-step', children=[
                    html.Span(className='pipeline-icon success-bg', children=[
                        html.I(className='fa-solid fa-check'),
                    ]),
                    html.Div([
                        html.Strong('Optimize'),
                        html.Small('Execute approved actions'),
                    ]),
                ]),
            ]),
        ]),

        # ── Decisions Table ───────────────────────────────────────────
        html.Div(className='panel', children=[
            html.Div(className='panel-head', children=[
                html.Div([
                    html.H3('Optimisation Decisions'),
                    html.P('Click a row to view the AI explanation'),
                ]),
            ]),
            dash_table.DataTable(
                id='d-table', columns=_COLS, data=[],
                row_selectable='single', selected_rows=[],
                page_size=10,
                style_table={'overflowX': 'auto'},
                style_header=_HDR, style_cell=_CELL,
                style_data_conditional=_COND,
                style_as_list_view=True,
            ),
        ]),

        # ── AI Explanation Detail ─────────────────────────────────────
        html.Div(
            id='d-detail', className='panel',
            style={'marginTop': '16px', 'display': 'none'},
            children=[
                html.Div(className='panel-head', children=[
                    html.Div([
                        html.H3([
                            html.I(className='fa-solid fa-robot accent-icon'),
                            ' AI Explanation',
                        ]),
                        html.P('Generated reasoning for this decision'),
                    ]),
                ]),
                html.Div(id='d-reason', style={
                    'fontSize': '13px', 'color': '#94a3b8',
                    'marginBottom': '12px',
                }),
                html.Div(id='d-explain', className='explanation-box'),
                html.Div(id='d-meta', style={
                    'fontSize': '11px', 'color': '#7c8ea6',
                    'marginTop': '12px',
                }),
            ],
        ),
    ])
