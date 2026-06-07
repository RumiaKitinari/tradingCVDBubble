from django.apps import AppConfig


class CandleAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'candle_app'
    
    def ready(self):
        # Delay the import to avoid circular import issues
        import sys
        if 'makemigrations' not in sys.argv and 'migrate' not in sys.argv:
            from .data_fetcher import fetch_and_store_nvidia_data
            # Avoid calling the function here to prevent side effects
