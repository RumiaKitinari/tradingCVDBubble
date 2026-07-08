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

app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            <script>
                window.dash_clientside = window.dash_clientside || {};
                window.dash_clientside.clientside = window.dash_clientside.clientside || {};
                window.dash_clientside.clientside.refit_y = function(relayoutData, figureData) {
                    // Triggered by EITHER user pan (relayoutData) OR data refresh (figureData)
                    
                    var trigger = dash_clientside.callback_context.triggered;
                    var isFigUpdate = trigger && trigger.length > 0 && trigger[0].prop_id === 'main-chart.figure';
                    
                    if (!isFigUpdate) {
                        if (!relayoutData) return window.dash_clientside.no_update;
                        var keys = Object.keys(relayoutData);
                        var hasX = keys.some(function(k) { return k.indexOf('xaxis') === 0; });
                        if (!hasX) return window.dash_clientside.no_update;
                    }

                    if (window.__refit_timer) {
                        clearTimeout(window.__refit_timer);
                    }

                    window.__refit_timer = setTimeout(function() {
                        if (window.__is_refitting) {
                            return; // Wait until current refit is done
                        }

                        var wrapper = document.getElementById('main-chart');
                        if (!wrapper) return;
                        var gd = wrapper.classList.contains('js-plotly-plot') ? wrapper : wrapper.querySelector('.js-plotly-plot');
                        if (!gd || !gd._fullLayout) return;
                        
                        var YPAD = 0.10;
                        var LEFT = {'y':'yaxis', 'y2':'yaxis2', 'y4':'yaxis4'};
                        var ax = gd._fullLayout.xaxis;
                        if (!ax || !ax.range) return;
                        var xr = [ax.range[0], ax.range[1]];
                        
                        var upd = {};
                        Object.keys(LEFT).forEach(function(yref) {
                            var lo = Infinity, hi = -Infinity;
                            gd._fullData.forEach(function(f) {
                                if (f.visible === false || f.visible === 'legendonly') return;
                                if ((f.yaxis || 'y') !== yref) return;
                                var xs = f.x; if (!xs || !xs.length) return;
                                
                                var offset = xs[0];
                                var len = xs.length;
                                var i0 = Math.max(0, Math.floor(xr[0] - offset));
                                var i1 = Math.min(len - 1, Math.ceil(xr[1] - offset));
                                if (i0 > i1 || i0 >= len || i1 < 0) return; 
                                
                                if (f.low && f.high) {
                                    var lows = f.low, highs = f.high;
                                    for (var i = i0; i <= i1; i++) {
                                        if (lows[i]  < lo) lo = lows[i];
                                        if (highs[i] > hi) hi = highs[i];
                                    }
                                } else if (f.y) {
                                    var ys = f.y;
                                    for (var i = i0; i <= i1; i++) {
                                        var v = ys[i];
                                        if (v == null || isNaN(v)) continue;
                                        if (v < lo) lo = v;
                                        if (v > hi) hi = v;
                                    }
                                }
                            });
                            if (lo < hi && lo !== Infinity) {
                                var pad = (hi - lo) * YPAD;
                                var new_lo = lo - pad;
                                var new_hi = hi + pad;
                                
                                var old_range = gd._fullLayout[LEFT[yref]] ? gd._fullLayout[LEFT[yref]].range : null;
                                if (!old_range || Math.abs(old_range[0] - new_lo) > 0.05 || Math.abs(old_range[1] - new_hi) > 0.05) {
                                    upd[LEFT[yref] + '.range'] = [new_lo, new_hi];
                                }
                            }
                        });
                        
                        if (Object.keys(upd).length > 0) {
                            window.__is_refitting = true;
                            Plotly.relayout(gd, upd).then(function() {
                                window.__is_refitting = false;
                            }).catch(function() {
                                window.__is_refitting = false;
                            });
                        }
                    }, 150);
                    
                    return window.dash_clientside.no_update;
                };
            </script>
            {%renderer%}
        </footer>
    </body>
</html>
'''

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
                    value='NVDA', 
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
                        {'label': ' Raw Ticks (IBKR)', 'value': 'raw_tick'},
                        {'label': ' 1-Sec Base (IBKR)', 'value': '1sec'},
                        {'label': ' 1-Min Base (FinViz)', 'value': 'i1'}
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
        html.Div(id='clientside-dummy', style={'display': 'none'}),
        
        # State tracking stores
        dcc.Store(id='last-data-state', data='{}'),
        dcc.Store(id='days-to-load', data=3)
        
        
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
        tfs = list(TIMEFRAME_RULE_IBKR.keys())
        new_value = "1min"  # Default to 1min to prevent UI lag from 1hr aggregation
    elif base_tf == '1sec':
        tfs = [t for t in TIMEFRAME_RULE_IBKR.keys() if t != 'raw_tick']
        new_value = current_value if current_value in tfs else "1hr"
    else: 
        tfs = list(TIMEFRAME_RULE.keys())
        new_value = current_value if current_value in tfs else "1hr"
        
    options = [{'label': t, 'value': t} for t in tfs]
    return options, new_value


# Global state to prevent spamming FinViz fetches
import time
last_finviz_fetch = {}
@app.callback(
    [Output('main-chart', 'figure'),
     Output('last-updated-text', 'children'),
     Output('loading-dummy', 'children'),
     Output('last-data-state', 'data')],
    [Input('ticker-input', 'value'),
     Input('source-radio', 'value'),
     Input('timeframe-dropdown', 'value'),
     Input('interval-component', 'n_intervals'),
     Input('refresh-btn', 'n_clicks'),
     Input('days-to-load', 'data')],
    [State('last-data-state', 'data')]
)
def update_graph(ticker, base_tf, active_tf, n_intervals, n_clicks, days_to_load, last_state_json):
    trigger = ctx.triggered_id
    
    if not ticker:
        raise PreventUpdate
    
    ticker = str(ticker).strip().upper()
    logging.info(f"Dash update triggered by {trigger} for {ticker} ({base_tf}) TF: {active_tf} Days: {days_to_load}")
    
    try:
        # Periodic FinViz fetch (every 60 seconds) or forced by Manual Refresh
        now = time.time()
        should_fetch_finviz = False
        
        if trigger == 'refresh-btn':
            should_fetch_finviz = True
        elif base_tf == 'i1':
            if ticker not in last_finviz_fetch or (now - last_finviz_fetch[ticker]) > 60:
                should_fetch_finviz = True
                
        if should_fetch_finviz:
            logging.info(f"Fetching latest FinViz data for {ticker}...")
            try:
                from finviz.new_finviz import fetch_and_save
                fetch_and_save(ticker, timeframe="i1")
                last_finviz_fetch[ticker] = now
            except Exception as e:
                logging.error(f"Auto-fetch failed: {e}")
                
        df_base, frames = run_pipeline(ticker, base_timeframe=base_tf, days=days_to_load)
        
        # Fallback Logic: If user requested raw_tick or 1sec, but DB has no data,
        # fallback to FinViz (i1) gracefully without changing the radio button to avoid circular dependency.
        fallback_msg = ""
        
        if df_base.empty and base_tf != 'i1':
            logging.info(f"No data for {ticker} in {base_tf}. Rendering FinViz i1 chart instead...")
            try:
                from finviz.new_finviz import fetch_and_save
                fetch_and_save(ticker, timeframe="i1")
                last_finviz_fetch[ticker] = now
                df_base, frames = run_pipeline(ticker, base_timeframe='i1', days=days_to_load)
                fallback_msg = f" (Warning: No {base_tf} tick data found. Displaying FinViz 1-Min instead)"
            except Exception as e:
                logging.error(f"Fallback fetch failed: {e}")
        
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
            return empty_fig, "No Data", "", "{}"
            
        import json
        
        # Optimization: Only re-render if data has actually grown/changed
        current_state = {
            "ticker": ticker,
            "base_tf": base_tf,
            "active_tf": active_tf,
            "len": len(df_base),
            "oldest_date": str(df_base.index[0]),
            "newest_date": str(df_base.index[-1]),
            "days_loaded": days_to_load
        }
        current_state_json = json.dumps(current_state)
        
        if current_state_json == last_state_json and trigger == 'interval-component':
            raise PreventUpdate
            
        fig = build_chart(df_base, frames, ticker, active_timeframe=active_tf)
        
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            uirevision=ticker,
        )
        
        import datetime
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        msg = f"Last Updated: {now_str} (Trigger: {trigger}){fallback_msg}"
        
        # Return fig, text, empty string for loading, and new state
        return fig, msg, "", current_state_json
        
    except PreventUpdate:
        raise
    except Exception as e:
        logging.error(f"Error building chart: {e}")
        import traceback
        traceback.print_exc()
        empty_fig = go.Figure()
        empty_fig.update_layout(template="plotly_dark", title=dict(text=f"Error: {e}", font=dict(color="red")))
        return empty_fig, f"Error: {e}", "", last_state_json

app.clientside_callback(
    dash.ClientsideFunction(
        namespace='clientside',
        function_name='refit_y'
    ),
    Output('clientside-dummy', 'children'),
    [Input('main-chart', 'relayoutData'),
     Input('main-chart', 'figure')]
)


# Infinite Scrolling Callback
@app.callback(
    Output('days-to-load', 'data'),
    Input('main-chart', 'relayoutData'),
    [State('days-to-load', 'data'),
     State('last-data-state', 'data')]
)
def handle_panning(relayout_data, current_days, last_state_json):
    if not relayout_data or 'xaxis.range[0]' not in relayout_data:
        raise PreventUpdate
        
    if not last_state_json or last_state_json == "{}":
        raise PreventUpdate
        
    try:
        import json
        import pandas as pd
        state = json.loads(last_state_json)
        oldest_date_str = state.get('oldest_date')
        
        if not oldest_date_str:
            raise PreventUpdate
            
        oldest_date = pd.to_datetime(oldest_date_str)
        view_start = pd.to_datetime(relayout_data['xaxis.range[0]'])
        
        if view_start.tzinfo is None:
            view_start = view_start.tz_localize('UTC')
        if oldest_date.tzinfo is None:
            oldest_date = oldest_date.tz_localize('UTC')
            
        # If panning approaches the oldest date loaded (within 12 hours)
        if view_start <= oldest_date + pd.Timedelta(hours=12):
            new_days = min(current_days * 2 + 1, 180) # Cap at 180 days
            if new_days > current_days:
                logging.info(f"Panning detected. Increasing loaded days: {current_days} -> {new_days}")
                return new_days
                
        raise PreventUpdate
    except PreventUpdate:
        raise
    except Exception as e:
        logging.error(f"Error in handle_panning: {e}")
        raise PreventUpdate


if __name__ == '__main__':
    app.run(debug=True, port=8050)
