import 'dart:async';

import 'package:dio/dio.dart';

import '../storage/token_storage.dart';

class ApiClient {
  ApiClient({required String baseUrl, required TokenStorage tokenStorage})
    : _tokenStorage = tokenStorage,
      dio = Dio(BaseOptions(baseUrl: baseUrl)) {
    dio.interceptors.add(
      InterceptorsWrapper(onRequest: _onRequest, onError: _onError),
    );
  }

  final Dio dio;
  final TokenStorage _tokenStorage;
  Future<bool>? _refreshing;

  bool _isPublicAuthPath(String path) {
    return const {
      '/auth/request-otp',
      '/auth/verify-otp',
      '/auth/refresh',
    }.contains(path);
  }

  Future<void> _onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    try {
      if (!_isPublicAuthPath(options.path)) {
        final tokens = await _tokenStorage.read();
        if (tokens != null) {
          options.headers['Authorization'] = 'Bearer ${tokens.accessToken}';
        }
      }
      handler.next(options);
    } catch (_) {
      handler.reject(
        DioException(
          requestOptions: options,
          type: DioExceptionType.unknown,
          message: 'Unable to read session storage',
        ),
      );
    }
  }

  Future<void> _onError(
    DioException error,
    ErrorInterceptorHandler handler,
  ) async {
    final request = error.requestOptions;
    final canRefresh =
        error.response?.statusCode == 401 &&
        request.extra['retried'] != true &&
        !_isPublicAuthPath(request.path);
    if (!canRefresh || !await _refreshOnce()) {
      handler.next(error);
      return;
    }

    final tokens = await _tokenStorage.read();
    request.extra['retried'] = true;
    request.headers['Authorization'] = 'Bearer ${tokens!.accessToken}';
    try {
      handler.resolve(await dio.fetch<dynamic>(request));
    } on DioException catch (retryError) {
      handler.next(retryError);
    }
  }

  Future<bool> _refreshOnce() {
    return _refreshing ??= _refresh().whenComplete(() => _refreshing = null);
  }

  Future<bool> _refresh() async {
    final tokens = await _tokenStorage.read();
    if (tokens == null) return false;
    try {
      final response = await Dio(BaseOptions(baseUrl: dio.options.baseUrl))
          .post<Map<String, dynamic>>(
            '/auth/refresh',
            data: {'refresh_token': tokens.refreshToken},
          );
      final data = response.data!;
      await _tokenStorage.write(
        AuthTokens(
          accessToken: data['access_token'] as String,
          refreshToken: data['refresh_token'] as String,
        ),
      );
      return true;
    } on DioException {
      await _tokenStorage.clear();
      return false;
    }
  }
}
