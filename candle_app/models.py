from django.db import models

# Create your models here.


class HistoricalData(models.Model):
    ticker = models.CharField(max_length=10)
    hour = models.DateTimeField()  # Store datetime
    open = models.DecimalField(max_digits=10, decimal_places=2)
    high = models.DecimalField(max_digits=10, decimal_places=2)
    low = models.DecimalField(max_digits=10, decimal_places=2)
    close = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()  # Store only the date part# The date of the data
