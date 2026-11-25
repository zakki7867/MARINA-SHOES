from django.urls import path
from .views import (
    ProfileView,
    UserCreateView,
    UserListView,
    UserLoginView,
    UserLogoutView,
    UserUpdateView,
)

app_name = "accounts"

urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("users/", UserListView.as_view(), name="user_list"),
    path("users/new/", UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", UserUpdateView.as_view(), name="user_edit"),
]
