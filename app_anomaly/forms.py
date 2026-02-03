from django import forms
from .models import AnomalyRecord


class AnomalyAnalysisForm(forms.Form):
    """
    Anomaliya tahlili uchun forma
    Skvajina, parametr, vaqt oralig'i, anomaliya davomiyligi va optional magnitude
    """

    # Dinamik choice fields uchun - backend'dan to'ldiriladi
    wells = forms.MultipleChoiceField(
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label='Skvajinalarni tanlang (kamida 1 ta)',
        error_messages={
            'required': 'Kamida bitta skvajina tanlang',
        }
    )

    parameters = forms.MultipleChoiceField(
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label='Parametrlarni tanlang (kamida 1 ta)',
        error_messages={
            'required': 'Kamida bitta parametr tanlang',
        }
    )

    time_period = forms.ChoiceField(
        required=True,
        choices=[
            (1, '1 oy'),
            (3, '3 oy'),
            (6, '6 oy'),
            (12, '12 oy'),
            (24, '24 oy'),
        ],
        widget=forms.RadioSelect,
        label='Vaqt oralig\'i',
        initial=6
    )

    anomaly_duration = forms.ChoiceField(
        required=True,
        choices=[
            (1, '1 kun'),
            (3, '3 kun'),
            (5, '5 kun'),
            (7, '7 kun'),
            (10, '10 kun'),
            (14, '14 kun'),
            (30, '30 kun'),
        ],
        widget=forms.RadioSelect,
        label='Minimal ketma-ket anomal qiymatlar soni',
        initial=3
    )

    magnitude = forms.FloatField(
        required=False,
        min_value=0.0,
        max_value=10.0,
        step_size=0.1,
        label='Magnitude (optional)',
        widget=forms.NumberInput(attrs={
            'placeholder': '0.0 - 10.0 (ixtiyoriy)',
            'step': '0.1',
            'class': 'form-control'
        })
    )

    sigma = forms.FloatField(
        required=False,
        min_value=0.5,
        max_value=5.0,
        initial=2.0,
        label='Sigma (σ) - Anomaliya aniqlash uchun',
        widget=forms.NumberInput(attrs={
            'step': '0.1',
            'class': 'form-control'
        })
    )

    class Meta:
        fields = ['wells', 'parameters', 'time_period', 'anomaly_duration', 'magnitude', 'sigma']

    def __init__(self, *args, wells_choices=None, params_choices=None, **kwargs):
        """
        Dinamik choice fields'larni to'ldirish
        Backend'dan wells va parameters list yuboriladi
        """
        super().__init__(*args, **kwargs)

        # Wells choices'ni o'rnatish
        if wells_choices:
            self.fields['wells'].choices = wells_choices

        # Parameters choices'ni o'rnatish
        if params_choices:
            self.fields['parameters'].choices = params_choices

    def clean(self):
        """
        Form validation
        """
        cleaned_data = super().clean()

        wells = cleaned_data.get('wells')
        parameters = cleaned_data.get('parameters')
        magnitude = cleaned_data.get('magnitude')
        sigma = cleaned_data.get('sigma')

        # Skvajinalarni validatsiya qilish
        if not wells:
            raise forms.ValidationError('Kamida bitta skvajina tanlang')

        # Parametrlarni validatsiya qilish
        if not parameters:
            raise forms.ValidationError('Kamida bitta parametr tanlang')

        # Magnitude (agar kiritilgan bo'lsa) validatsiya qilish
        if magnitude is not None and (magnitude < 0 or magnitude > 10):
            raise forms.ValidationError('Magnitude 0 dan 10 gacha bo\'lishi kerak')

        # Sigma validatsiya
        if sigma and (sigma < 0.5 or sigma > 5.0):
            raise forms.ValidationError('Sigma 0.5 dan 5.0 gacha bo\'lishi kerak')

        return cleaned_data