// Masha UI: standalone "Connect to remote device" screen.
// Reuses the existing IDTextEditingController + connect() logic, only the layout is new.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:get/get.dart';

import '../../common.dart';
import '../../common/formatter/id_formatter.dart';

const _mashaAccent = Color(0xFF6F973A);

class MashaConnectPage extends StatefulWidget {
  const MashaConnectPage({Key? key}) : super(key: key);

  @override
  State<MashaConnectPage> createState() => _MashaConnectPageState();
}

class _MashaConnectPageState extends State<MashaConnectPage> {
  final _idController = IDTextEditingController();
  final _idEditingController = TextEditingController();
  final _passwordController = TextEditingController();

  @override
  void initState() {
    super.initState();
    if (_idController.text.isEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        final lastRemoteId = await bind.mainGetLastRemoteId();
        if (mounted && lastRemoteId != _idController.id) {
          setState(() {
            _idController.id = lastRemoteId;
            _idEditingController.text = formatID(lastRemoteId);
          });
        }
      });
    }
    if (Get.isRegistered<IDTextEditingController>()) {
      Get.delete<IDTextEditingController>();
    }
    Get.put<IDTextEditingController>(_idController);
  }

  @override
  void dispose() {
    _idController.dispose();
    _idEditingController.dispose();
    _passwordController.dispose();
    if (Get.isRegistered<IDTextEditingController>()) {
      Get.delete<IDTextEditingController>();
    }
    super.dispose();
  }

  void _onConnect() {
    connect(context, _idController.id,
        password:
            _passwordController.text.isEmpty ? null : _passwordController.text);
  }

  Future<void> _pasteId() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim();
    if (text == null || text.isEmpty) return;
    setState(() {
      _idController.id = text;
      _idEditingController.text = formatID(text);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: _mashaAccent,
      padding: const EdgeInsets.fromLTRB(40, 32, 40, 40),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                translate('Подключиться к удалённому устройству'),
                textAlign: TextAlign.center,
                style: const TextStyle(
                    color: Colors.white,
                    fontSize: 26,
                    fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 22),
              Text(translate('Укажите ID удалённого устройства:'),
                  style: _labelStyle),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _idEditingController,
                      inputFormatters: [IDTextInputFormatter()],
                      style: const TextStyle(color: Colors.white, fontSize: 18),
                      textAlign: TextAlign.center,
                      decoration: _fieldDecoration('000 000 000'),
                      onChanged: (v) => _idController.id = v,
                      onSubmitted: (_) => _onConnect(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton(
                    tooltip: translate('Paste'),
                    icon: const Icon(Icons.paste, color: Colors.white),
                    onPressed: _pasteId,
                  ),
                ],
              ),
              const SizedBox(height: 18),
              Text(translate('Укажите пароль удалённого устройства'),
                  style: _labelStyle),
              const SizedBox(height: 8),
              TextField(
                controller: _passwordController,
                obscureText: true,
                style: const TextStyle(color: Colors.white, fontSize: 18),
                textAlign: TextAlign.center,
                decoration: _fieldDecoration('••••••••'),
                onSubmitted: (_) => _onConnect(),
              ),
              const SizedBox(height: 26),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: ElevatedButton(
                  onPressed: _onConnect,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.white,
                    foregroundColor: _mashaAccent,
                  ),
                  child: Text(translate('Connect'),
                      style:
                          const TextStyle(fontWeight: FontWeight.bold)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

const _labelStyle = TextStyle(
    color: Colors.white,
    fontSize: 13,
    fontWeight: FontWeight.bold,
    letterSpacing: .5);

InputDecoration _fieldDecoration(String hint) => InputDecoration(
      hintText: hint,
      hintStyle: const TextStyle(color: Colors.white54),
      filled: true,
      fillColor: Colors.white.withValues(alpha: 0.12),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(6),
        borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.4)),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(6),
        borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.4)),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(6),
        borderSide: const BorderSide(color: Colors.white),
      ),
    );
