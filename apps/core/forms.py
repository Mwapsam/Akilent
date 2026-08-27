from django import forms

from .models import Configurations


class ConfigurationForm(forms.ModelForm):
    class Meta:
        model = Configurations
        fields = ["name", "value"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Configuration name",
            }),
            "value": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Configuration value",
                "rows": 4,
            }),
        }