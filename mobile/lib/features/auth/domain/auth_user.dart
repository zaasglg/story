enum UserRole {
  worker,
  master,
  admin,
  boss;

  static UserRole fromApi(String value) {
    return UserRole.values.firstWhere(
      (role) => role.name.toUpperCase() == value,
      orElse: () => throw FormatException('Unknown role: $value'),
    );
  }
}

class AuthUser {
  const AuthUser({
    required this.id,
    required this.phone,
    required this.role,
    required this.status,
    this.fullName,
    this.employeeId,
    this.city,
  });

  factory AuthUser.fromJson(Map<String, dynamic> json) {
    return AuthUser(
      id: json['id'] as String,
      phone: json['phone'] as String,
      role: UserRole.fromApi(json['role'] as String),
      status: json['status'] as String,
      fullName: json['full_name'] as String?,
      employeeId: json['employee_id'] as String?,
      city: json['city'] as String?,
    );
  }

  final String id;
  final String phone;
  final UserRole role;
  final String status;
  final String? fullName;
  final String? employeeId;
  final String? city;
}
