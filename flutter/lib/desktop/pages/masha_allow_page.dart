// Masha UI: standalone "Allow connection to this device" screen.
// Reuses the existing ServerModel fields/handlers, only the layout is new.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../common.dart';
import '../../models/model.dart';
import '../../models/state_model.dart';
import '../../models/server_model.dart';

const _mashaAccentDark = Color(0xFF3F5C1F);

class MashaAllowPage extends StatelessWidget {
  const MashaAllowPage({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider.value(
      value: gFFI.serverModel,
      child: Consumer<ServerModel>(
        builder: (context, model, child) {
          final showOneTime = model.approveMode != 'click' &&
              model.verificationMethod != kUsePermanentPassword;
          return Container(
            color: _mashaAccentDark,
            padding: const EdgeInsets.fromLTRB(40, 32, 40, 40),
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      translate('Разрешить подключение к данному устройству'),
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          color: Colors.white,
                          fontSize: 26,
                          fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 22),
                    Text(translate('ID вашего устройства:'), style: _labelStyle),
                    const SizedBox(height: 8),
                    _CopyableField(
                      controller: model.serverId,
                      onCopy: () {
                        Clipboard.setData(
                            ClipboardData(text: model.serverId.text));
                        showToast(translate("Copied"));
                      },
                    ),
                    const SizedBox(height: 18),
                    if (showOneTime) ...[
                      Text(translate('Сообщите этот пароль удалённому оператору'),
                          style: _labelStyle),
                      const SizedBox(height: 8),
                      _CopyableField(
                        controller: model.serverPasswd,
                        onCopy: () {
                          Clipboard.setData(
                              ClipboardData(text: model.serverPasswd.text));
                          showToast(translate("Copied"));
                        },
                        trailing: IconButton(
                          tooltip: translate('Refresh Password'),
                          icon: const Icon(Icons.refresh, color: Colors.white),
                          onPressed: () => bind.mainUpdateTemporaryPassword(),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _CopyableField extends StatelessWidget {
  const _CopyableField(
      {required this.controller, required this.onCopy, this.trailing});

  final TextEditingController controller;
  final VoidCallback onCopy;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 56,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(6),
        color: Colors.white.withOpacity(0.12),
        border: Border.all(color: Colors.white.withOpacity(0.4)),
      ),
      child: Row(
        children: [
          Expanded(
            child: GestureDetector(
              onDoubleTap: onCopy,
              child: TextFormField(
                controller: controller,
                readOnly: true,
                textAlign: TextAlign.center,
                decoration: const InputDecoration(border: InputBorder.none),
                style: const TextStyle(
                    color: Colors.white,
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                    letterSpacing: 1),
              ),
            ),
          ),
          IconButton(
            tooltip: translate('Copy'),
            icon: const Icon(Icons.copy, color: Colors.white, size: 18),
            onPressed: onCopy,
          ),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }
}

const _labelStyle = TextStyle(
    color: Colors.white,
    fontSize: 13,
    fontWeight: FontWeight.bold,
    letterSpacing: .5);
