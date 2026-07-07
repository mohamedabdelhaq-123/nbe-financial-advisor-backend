from django import forms
from django.contrib import admin
from django.contrib.auth.hashers import make_password

from core.models import AdminUser


class AdminUserForm(forms.ModelForm):
    """
    Plain-text password field for Django admin's create/edit form. AdminUser
    is a structurally separate credential space from core.User (see
    core/authentication.py) and isn't AbstractBaseUser-based, so it has no
    set_password()/change-password machinery of its own — without this
    form, staff provisioning an admin account through Django's built-in
    admin site (System_Architecture.md §9) would have to type an
    already-hashed value directly into `password_hash`, which is exactly
    the kind of thing that ends up with a plaintext password stored by
    mistake. This is the one place AdminUser accounts are meant to be
    created — there's no public self-signup endpoint (API_Endpoints_1.md
    §12 lists no POST /admin/auth/signup), matching
    Data_Governance_Specs.md §8: "Access is restricted to internal staff
    roles."
    """

    password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        help_text="Leave blank to keep the current password when editing an existing admin.",
    )

    class Meta:
        model = AdminUser
        fields = ["name", "email", "role"]

    def save(self, commit=True):
        instance = super().save(commit=False)
        raw_password = self.cleaned_data.get("password")
        if raw_password:
            instance.password_hash = make_password(raw_password)
        elif not instance.password_hash:
            raise forms.ValidationError("Password is required for a new admin user.")
        if commit:
            instance.save()
        return instance


@admin.register(AdminUser)
class AdminUserAdmin(admin.ModelAdmin):
    form = AdminUserForm
    list_display = ["name", "email", "role", "created_at"]
