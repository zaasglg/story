class WorkerSummary {
  const WorkerSummary({
    required this.id,
    required this.fullName,
    required this.city,
    required this.brigadeName,
    this.employeeId,
    this.phone,
    this.passportNumber,
    this.objectName,
  });

  factory WorkerSummary.fromJson(Map<String, dynamic> json) {
    final assignment = json['assignment'] as Map<String, dynamic>?;
    return WorkerSummary(
      id: json['id'] as String,
      fullName: json['full_name'] as String,
      city: json['city'] as String?,
      brigadeName:
          (assignment?['brigade_name'] ?? json['brigade_name']) as String?,
      employeeId: json['employee_id'] as String?,
      phone: json['phone'] as String?,
      passportNumber: json['passport_number'] as String?,
      objectName: assignment?['object_name'] as String?,
    );
  }

  final String id;
  final String fullName;
  final String? city;
  final String? brigadeName;
  final String? employeeId;
  final String? phone;
  final String? passportNumber;
  final String? objectName;
}

class WorkObjectSummary {
  const WorkObjectSummary({
    required this.id,
    required this.name,
    required this.address,
    required this.status,
  });

  factory WorkObjectSummary.fromJson(Map<String, dynamic> json) {
    return WorkObjectSummary(
      id: json['id'] as String,
      name: json['name'] as String,
      address: json['address'] as String,
      status: json['status'] as String,
    );
  }

  final String id;
  final String name;
  final String address;
  final String status;
}
