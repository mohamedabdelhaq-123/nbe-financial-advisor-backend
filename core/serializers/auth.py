from django.contrib.auth import authenticate
from rest_framework import serializers

from core.models import User


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "name", "email", "password", "phone"]
        read_only_fields = ["id"]
        # DRF auto-generates a UniqueValidator for `email` from the model's
        # unique=True constraint, so a duplicate signup surfaces as a normal
        # 422 validation error (via core/exceptions.py) rather than an
        # unhandled IntegrityError.

    def create(self, validated_data):
        # UserManager.create_user() calls set_password() internally — never
        # assign validated_data["password"] directly onto the instance, that
        # would store it in plaintext.
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = authenticate(
            self.context["request"], username=attrs["email"], password=attrs["password"]
        )
        if user is None:
            # Deliberately generic: doesn't distinguish "no such email" from
            # "wrong password", so a login attempt can't be used to enumerate
            # which emails are registered.
            raise serializers.ValidationError("Invalid email or password.")
        attrs["user"] = user
        return attrs


class RefreshRequestSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()
