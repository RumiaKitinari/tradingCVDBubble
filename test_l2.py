from cvd.calculator import run_pipeline
from level2_webapp.data_provider import fetch_and_aggregate_l2_data

df_base, frames = run_pipeline("NVDA", base_timeframe="raw_tick", days=0.01)
print(f"Base data length: {len(df_base)}")

df = frames["1min"]
print(f"1min data length: {len(df)}")
df_res, y_levels, z_matrix = fetch_and_aggregate_l2_data("NVDA", df, max_candles=300)

print("y_levels:", y_levels is not None)
if z_matrix is not None:
    print("z_matrix shape:", z_matrix.shape)
    print("z_matrix max value:", z_matrix.max())
else:
    print("z_matrix is None")
