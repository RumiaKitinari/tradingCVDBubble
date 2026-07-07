import dash
from dash import dcc, html, Input, Output
app = dash.Dash(__name__)
app.layout = html.Div([dcc.Input(id='in'), html.Div(id='out')])
app.clientside_callback(
    """
    function(val) { return val; }
    """,
    Output('out', 'children'), Input('in', 'value')
)
if __name__ == '__main__':
    print("Valid")
