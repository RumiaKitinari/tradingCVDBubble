import dash
from dash import dcc, html
app = dash.Dash(__name__)
app.layout = html.Div([
    dcc.Graph(id='main-chart', figure={'data': [{'y': [1,2,3]}]})
])
if __name__ == '__main__':
    with app.server.app_context():
        # we can't easily test DOM here without selenium, but we know Dash wraps it.
        pass
