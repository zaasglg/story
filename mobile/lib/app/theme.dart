import 'package:flutter/material.dart';

ThemeData buildAppTheme() {
  const navy = Color(0xFF101D32);
  const gold = Color(0xFFC49A4A);
  return ThemeData(
    colorScheme: ColorScheme.fromSeed(
      seedColor: navy,
      primary: navy,
      secondary: gold,
      surface: const Color(0xFFF6F4EF),
    ),
    scaffoldBackgroundColor: const Color(0xFFF6F4EF),
    useMaterial3: true,
    inputDecorationTheme: const InputDecorationTheme(
      border: OutlineInputBorder(),
      filled: true,
      fillColor: Colors.white,
    ),
  );
}
