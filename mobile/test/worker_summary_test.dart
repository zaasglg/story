import 'package:flutter_test/flutter_test.dart';
import 'package:m1917/features/workforce/domain/worker_summary.dart';

void main() {
  test('public worker projection has no private fields', () {
    final worker = WorkerSummary.fromJson({
      'id': 'worker-1',
      'full_name': 'Public Worker',
      'city': 'Lipetsk',
      'brigade_name': 'Brigade 17',
      'photo_url': null,
    });

    expect(worker.fullName, 'Public Worker');
    expect(worker.phone, isNull);
    expect(worker.passportNumber, isNull);
    expect(worker.employeeId, isNull);
  });

  test('master assignment is parsed without requiring private data', () {
    final worker = WorkerSummary.fromJson({
      'id': 'worker-1',
      'full_name': 'Assigned Worker',
      'city': 'Kursk',
      'photo_url': null,
      'employee_id': 'EMP-101',
      'assignment': {
        'brigade_name': 'Brigade 5',
        'object_name': 'Object Alpha',
      },
    });

    expect(worker.employeeId, 'EMP-101');
    expect(worker.brigadeName, 'Brigade 5');
    expect(worker.objectName, 'Object Alpha');
    expect(worker.phone, isNull);
  });
}
