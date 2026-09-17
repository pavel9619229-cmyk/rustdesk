# Правила выпуска сборок МАШИ

## Имя публичного Windows EXE

Единственный разрешённый формат имени пользовательского Windows-файла:

`masha-remote-operator-<номер_сборки>.exe`

Примеры: `masha-remote-operator-74.exe`, `masha-remote-operator-75.exe`.

Номер берётся из build number в `flutter/pubspec.yaml` (часть после `+`).
Форматы с `build-`, `windows`, `x86_64`, `64bit` или без номера сборки не использовать.
`build.py` обязан формировать имя автоматически по этому правилу.