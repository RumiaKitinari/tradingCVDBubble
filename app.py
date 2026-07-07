import dash
from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc
from cvd.calculator import run_pipeline
from cvd.visualizer import build_chart
import plotly.graph_objects as go
import logging

logging.basicConfig(level=logging.INFO)

# Use a Dark Bootstrap theme (CYBORG) for a sleek foundation
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG])

app.layout = html.Div([
    # Navbar / Header
    dbc.NavbarSimple(
        brand="Trading CVD Bubble Dashboard",
        brand_href="#",
        color="dark",
        dark=True,
        className="mb-4",
        style={"borderBottom": "1px solid #333", "boxShadow": "0 4px 6px rgba(0,0,0,0.3)"}
    ),
    
    dbc.Container([
        dbc.Row([
            dbc.Col([
                html.Label("Select Ticker:", style={"fontWeight": "bold", "color": "#ccc"}),
                dcc.Dropdown(
                    id='ticker-dropdown',
                    options=[
                        {'label': 'NVDA', 'value': 'NVDA'},
                        {'label': 'AAPL', 'value': 'AAPL'},
                        {'label': 'TSLA', 'value': 'TSLA'},
                        {'label': 'MOCK_NVDA (Test Ticks)', 'value': 'MOCK_NVDA'}
                    ],
                    value='MOCK_NVDA',
                    clearable=False,
                    style={"color": "#000"} # Dropdown text color
                )
            ], width=3),
            
            dbc.Col([
                html.Label("Base Data Source:", style={"fontWeight": "bold", "color": "#ccc"}),
                dcc.RadioItems(
                    id='source-radio',
                    options=[
                        {'label': ' Raw Ticks (1-Tick)', 'value': 'raw_tick'},
                        {'label': ' 1-Sec Aggregated', 'value': '1sec'},
                        {'label': ' FinViz (1-Min)', 'value': 'i1'}
                    ],
                    value='raw_tick',
                    inline=True,
                    className="mt-2",
                    labelStyle={"marginRight": "15px"}
                )
            ], width=6),
            
            dbc.Col([
                html.Div(id='last-updated-text', className="mt-4 text-muted text-end")
            ], width=3)
        ], className="mb-4"),
        
        # Main Chart Area with Glassmorphism wrapper
        html.Div([
            dcc.Graph(
                id='main-chart',
                style={'height': '1150px'},
                config={'scrollZoom': True, 'displayModeBar': False}
            )
        ], style={
            "padding": "10px", 
            "backgroundColor": "rgba(30, 30, 30, 0.6)",
            "backdropFilter": "blur(10px)",
            "borderRadius": "12px",
            "border": "1px solid rgba(255, 255, 255, 0.1)",
            "boxShadow": "0 8px 32px 0 rgba(0, 0, 0, 0.37)"
        }),
        
        # Auto-refresh interval (every 3 seconds)
        dcc.Interval(
            id='interval-component',
            interval=3 * 1000, 
            n_intervals=0
        )
    ], fluid=True, style={"padding": "0 2% 50px 2%"})
], style={"backgroundColor": "#121212", "minHeight": "100vh"})


@app.callback(
    [Output('main-chart', 'figure'),
     Output('last-updated-text', 'children')],
    [Input('ticker-dropdown', 'value'),
     Input('source-radio', 'value'),
     Input('interval-component', 'n_intervals')]
)
def update_graph(ticker, base_tf, n):
    logging.info(f"[{n}] Dash update triggered for {ticker} ({base_tf})")
    
    try:
        df_base, frames = run_pipeline(ticker, base_timeframe=base_tf)
        
        if df_base.empty:
            empty_fig = go.Figure()
            empty_fig.update_layout(
                template="plotly_dark", 
                title=f"No data available for {ticker} in MongoDB.",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)"
            )
            return empty_fig, "No Data"
            
        fig = build_chart(df_base, frames, ticker)
        
        # Remove the paper background so the glassmorphism div shows through
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        
        import datetime
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        return fig, f"Last Updated: {now_str}"
        
    except Exception as e:
        logging.error(f"Error building chart: {e}")
        import traceback
        traceback.print_exc()
        # Return a blank figure to prevent crashing the UI
        return dash.no_update, "Update Failed"

if __name__ == '__main__':
    app.run(debug=True, port=8050)
