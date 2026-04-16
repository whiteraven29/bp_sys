from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User


class TeacherRegistrationForm(UserCreationForm):
    full_name = forms.CharField(
        max_length=200, required=True,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Jane Mwangi', 'autocomplete': 'name'}),
    )
    username = forms.CharField(
        widget=forms.TextInput(attrs={'placeholder': 'e.g. jmwangi', 'autocomplete': 'username'}),
    )
    password1 = forms.CharField(
        label='Password', widget=forms.PasswordInput(attrs={'placeholder': '••••••••'}),
    )
    password2 = forms.CharField(
        label='Confirm Password', widget=forms.PasswordInput(attrs={'placeholder': '••••••••'}),
    )

    class Meta:
        model = User
        fields = ['username', 'full_name', 'password1', 'password2']


class StyledAuthForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={'placeholder': 'Username', 'autocomplete': 'username'}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': '••••••••', 'autocomplete': 'current-password'}),
    )
