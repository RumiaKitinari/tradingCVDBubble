from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name = 'candle-home'),
    path('real-time-chart/', views.real_time_chart, name='real_time_chart'),
]
    

