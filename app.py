import dash
from dash import dcc, html, Input, Output, State, ctx
import dash_bootstrap_components as dbc
from cvd.calculator import run_pipeline, TIMEFRAME_RULE_IBKR, TIMEFRAME_RULE
from cvd.visualizer import build_chart
import plotly.graph_objects as go
import logging
from dash.exceptions import PreventUpdate

logging.basicConfig(level=logging.INFO)

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG])

app.layout = html.Div([
    dbc.NavbarSimple(
        brand="Trading CVD Bubble Dashboard",
        brand_href="#",
        color="dark",
        dark=True,
        className="mb-3",
        style={"borderBottom": "1px solid #222", "boxShadow": "0 4px 10px rgba(0,0,0,0.5)"}
    ),
    
    dbc.Container([
        dbc.Row([
            dbc.Col([
                html.Label("Search Ticker:", style={"fontWeight": "bold", "color": "#eee"}),
                dbc.Input(
                    id='ticker-input', 
                    value='MOCK_NVDA', 
                    type='text', 
                    debounce=True, 
                    placeholder="Enter Ticker & hit Enter...",
                    style={"color": "#000", "backgroundColor": "white"}
                )
            ], width=2),
            
            dbc.Col([
                html.Label("Base Data Source:", style={"fontWeight": "bold", "color": "#eee"}),
                dcc.RadioItems(
                    id='source-radio',
                    options=[
                        {'label': ' Raw Ticks', 'value': 'raw_tick'},
                        {'label': ' 1-Sec Agg', 'value': '1sec'},
                        {'label': ' FinViz 1-Min', 'value': 'i1'}
                    ],
                    value='raw_tick',
                    inline=True,
                    className="mt-1",
                    labelStyle={"marginRight": "12px", "color": "#ccc"}
                )
            ], width=3),
            
            dbc.Col([
                html.Label("Active Timeframe:", style={"fontWeight": "bold", "color": "#eee"}),
                dcc.Dropdown(
                    id='timeframe-dropdown',
                    options=[], # Populated by callback
                    value='1hr',
                    clearable=False,
                    style={"color": "#000"} 
                )
            ], width=2),
            
            dbc.Col([
                html.Button("Manual Refresh", id="refresh-btn", className="btn btn-outline-info btn-sm mt-4 w-100")
            ], width=1),
            
            dbc.Col([
                html.Div(id='last-updated-text', className="mt-4 text-muted text-end", style={"fontSize": "14px", "marginRight": "10px"})
            ], width=3),
            
            # Isolated Loading Spinner (does not wrap the main chart)
            dbc.Col([
                html.Div([
                    dcc.Loading(
                        id="loading-spinner",
                        type="circle",
                        color="#29b6f6",
                        children=html.Div(id='loading-dummy', style={"width": "30px", "height": "30px"})
                    )
                ], className="mt-3")
            ], width=1)
            
        ], className="mb-3 align-items-center"),
        
        # Main Chart Area
        html.Div([
            dcc.Graph(
                id='main-chart',
                style={'height': '1100px'},
                config={'scrollZoom': True, 'displayModeBar': False}
            )
        ], style={
            "padding": "10px", 
            "backgroundColor": "rgba(10, 10, 10, 0.8)", 
            "backdropFilter": "blur(15px)",
            "borderRadius": "12px",
            "border": "1px solid rgba(255, 255, 255, 0.1)",
            "boxShadow": "0 8px 32px 0 rgba(0, 0, 0, 0.5)"
        }),
        
        dcc.Interval(
            id='interval-component',
            interval=10 * 1000, 
            n_intervals=0
        ),
        
        # Dummy div for clientside callback to prevent invalid ID errors
        html.Div(id='clientside-dummy', style={'display': 'none'})
        
    ], fluid=True, style={"padding": "0 2% 50px 2%"})
], style={"backgroundColor": "#0d0d0d", "minHeight": "100vh"})


# ── Callbacks ──

@app.callback(
    [Output('timeframe-dropdown', 'options'),
     Output('timeframe-dropdown', 'value')],
    [Input('source-radio', 'value')],
    [State('timeframe-dropdown', 'value')]
)
def update_timeframes(base_tf, current_value):
    if base_tf == 'raw_tick':
        tfs = ["raw_tick"] + list(TIMEFRAME_RULE_IBKR.keys())
    elif base_tf == '1sec':
        tfs = list(TIMEFRAME_RULE_IBKR.keys())
    else: 
        tfs = list(TIMEFRAME_RULE.keys())
        
    options = [{'label': t, 'value': t} for t in tfs]
    new_value = current_value if current_value in tfs else ("1hr" if "1hr" in tfs else tfs[-1])
    return options, new_value


@app.callback(
    [Output('main-chart', 'figure'),
     Output('last-updated-text', 'children'),
     Output('loading-dummy', 'children')],
    [Input('ticker-input', 'value'),
     Input('source-radio', 'value'),
     Input('timeframe-dropdown', 'value'),
     Input('interval-component', 'n_intervals'),
     Input('refresh-btn', 'n_clicks')]
)
def update_graph(ticker, base_tf, active_tf, n_intervals, n_clicks):
    trigger = ctx.triggered_id
    if not ticker:
        raise PreventUpdate
    
    ticker = str(ticker).strip().upper()
    logging.info(f"Dash update triggered by {trigger} for {ticker} ({base_tf}) TF: {active_tf}")
    
    try:
        df_base, frames = run_pipeline(ticker, base_timeframe=base_tf)
        
        # Auto-Fetch Logic for missing FinViz data
        if df_base.empty:
            logging.info(f"No data for {ticker}. Attempting auto-fetch...")
            try:
                from finviz.new_finviz import fetch_and_save
                fetch_and_save(ticker, timeframe="i1")
                df_base, frames = run_pipeline(ticker, base_timeframe=base_tf)
            except Exception as e:
                logging.error(f"Auto-fetch failed: {e}")
        
        if df_base.empty:
            empty_fig = go.Figure()
            empty_fig.update_layout(
                template="plotly_dark", 
                title=dict(text=f"No data available for {ticker} in MongoDB (and auto-fetch failed).", font=dict(color="white")),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False)
            )
            return empty_fig, "No Data", ""
            
        fig = build_chart(df_base, frames, ticker, active_timeframe=active_tf)
        
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        
        import datetime
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        msg = f"Last Updated: {now_str} (Trigger: {trigger})"
        
        # Return fig, text, and empty string for the loading dummy
        return fig, msg, ""
        
    except Exception as e:
        logging.error(f"Error building chart: {e}")
        import traceback
        traceback.print_exc()
        raise PreventUpdate


app.clientside_callback(
    dash.ClientsideFunction(
        namespace='clientside',
        function_name='refit_y'
    ),
    Output('clientside-dummy', 'children'),
    Input('main-chart', 'relayoutData')
)


if __name__ == '__main__':
    app.run(debug=True, port=8050)
