#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Meson Development Team

from __future__ import annotations
import typing as T
import datetime
import os
from collections import defaultdict

from mesonbuild.mesonlib import MachineChoice
from mesonbuild.convert.build_systems.common import (
    ConvertAttr,
    ConvertAttrNode,
    ConvertTarget,
    CommonEmitter,
    ConvertStateTracker,
    SelectNode,
    COMMON_INDENT,
    ConvertFileGroup,
    ConvertPythonTarget,
    ConvertCustomTarget,
    ConvertIncludeDirectory,
    ConvertFlag,
    ConvertStaticLibrary,
    ConvertSharedLibrary,
)
from mesonbuild.convert.build_systems.bazel.state import BazelBackend

COPYRIGHT_HEADER_TEMPLATE = """\
# Copyright (C) 2025-2026 The Magma GPU Project
# SPDX-License-Identifier: Apache-2.0
#
# Generated via:
#   https://github.com/mesonbuild/meson/tree/master/mesonbuild/convert
#
# Submit patches, do not hand-edit.
"""

LICENSE_BLOCK_TEMPLATE = """\
package(
    default_applicable_licenses = ["//:{root_license_name}"],
    default_visibility = ["//visibility:public"],
)
"""

ROOT_LICENSE_TEMPLATE = """\
license(
    name = "{license_name}",
    license_kinds = [
{license_kinds}
    ],
)
"""

BAZEL_ATTR_MAP = {
    ConvertAttr.NAME: "name",
    ConvertAttr.SRCS: "srcs",
    ConvertAttr.INCLUDES: "includes",
    ConvertAttr.BAZEL_DEPS: "deps",
    ConvertAttr.BAZEL_DEFINES: "defines",
    ConvertAttr.RUSTFLAGS: "rustc_flags",
    ConvertAttr.OUT: "outs",
    ConvertAttr.TOOLS: "tools",
    ConvertAttr.PYTHON_MAIN: "main",
    ConvertAttr.RUST_CRATE_NAME: "crate_name",
    ConvertAttr.RUST_EDITION: "edition",
    ConvertAttr.LDFLAGS: "linkopts",
}

PYTHON_LOAD_TEMPLATE = 'load("@rules_python//python:py_binary.bzl", "py_binary")'
LICENSE_LOAD_TEMPLATE = 'load("@rules_license//rules:license.bzl", "license")'
CC_LIBRARY_LOAD_TEMPLATE = 'load("@rules_cc//cc:defs.bzl", "cc_library")'

BAZEL_MODULE_TEMPLATE = """\
module(name = "{project_name}", version = "1.0")

bazel_dep(name = "rules_cc", version = "0.2.14")
bazel_dep(name = "platforms", version = "1.0.0")
bazel_dep(name = "rules_license", version = "1.0.0")
bazel_dep(name = "rules_python", version = "1.7.0")

http_archive = use_repo_rule("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

{http_archives}

{register_toolchains}

{python_setup}
"""

PYTHON_MODULE_SETUP_TEMPLATE = """\
python = use_extension("@rules_python//python/extensions:python.bzl", "python")
python.toolchain(
    python_version = "3.10",
    is_default = True,
)

pip = use_extension("@rules_python//python/extensions:pip.bzl", "pip")
pip.parse(
    hub_name = "meson_python_deps",
    python_version = "3.10",
    requirements_lock = "//bazel:requirements.txt",
)

use_repo(pip, "meson_python_deps")
"""

HTTP_ARCHIVE_TEMPLATE = """\
http_archive(
    name = "{name}",
    url = "{url}",
    sha256 = "{sha256}",
    build_file = "//bazel/toolchains:{name}_compiler.BUILD",
    type = "{file_type}"
)
"""

GENERAL_HTTP_ARCHIVE_TEMPLATE = """\
http_archive(
    name = "{name}",
    url = "{url}",
    sha256 = "{sha256}",
)
"""

COMPILER_MAPPING_TEMPLATE = """\
package(default_visibility = ["//visibility:public"])

filegroup(
    name = "all_files",
    srcs = glob(["**/*"]),
)
"""

TOOLCHAINS_BUILD_TEMPLATE = """\
load("@bazel_tools//tools/cpp:unix_cc_toolchain_config.bzl", "cc_toolchain_config")
load("@rules_cc//cc:defs.bzl", "cc_toolchain")

cc_toolchain_config(
    name = "{name}_config",
    cpu = "{cpu}",
    compiler = "gcc",
    toolchain_identifier = "{name}",
    host_system_name = "local",
    target_system_name = "{cpu}-{os}",
    target_libc = "unknown",
    abi_version = "unknown",
    abi_libc_version = "unknown",
    tool_paths = {{
        {tool_paths}
    }},
)

cc_toolchain(
    name = "{name}_cc_toolchain",
    all_files = "@{name}//:all_files",
    compiler_files = "@{name}//:all_files",
    dwp_files = ":empty",
    linker_files = "@{name}//:all_files",
    objcopy_files = "@{name}//:all_files",
    strip_files = "@{name}//:all_files",
    toolchain_config = ":{name}_config",
    supports_param_files = 0,
)

toolchain(
    name = "{name}_toolchain",
    target_compatible_with = [
        "@platforms//cpu:{cpu}",
        "@platforms//os:{os}",
    ],
    toolchain = ":{name}_cc_toolchain",
    toolchain_type = "@bazel_tools//tools/cpp:toolchain_type",
)
"""

PLATFORMS_BUILD_TEMPLATE = """\
package(default_visibility = ["//visibility:public"])

platform(
    name = "{name}_platform",
    constraint_values = [
        "@platforms//cpu:{cpu}",
        "@platforms//os:{os}",
    ],
)
"""


def _format_select_value(value: T.Union[str, bool]) -> str:
    # This needs to map to a config_setting label
    # For now, just return the string
    return f'"//{value}"'


def _emit_attribute_values(current_indent: int, attribute_values: T.List[str]) -> str:
    if not attribute_values:
        return "[]"

    default_indent = " " * current_indent
    list_indent = " " * (current_indent + COMMON_INDENT)
    content_str = "[\n"
    for value in attribute_values:
        content_str += f'{list_indent}"{value}",\n'
    content_str += f"{default_indent}]"
    return content_str


def _emit_select_values(indent: int, select_node: SelectNode) -> str:
    content_str = ""
    value_indent = indent + COMMON_INDENT
    indent_str = " " * value_indent
    for select_values, attribute_values in select_node.select_tuples:
        # This is a simplification. Real Bazel select needs a proper mapping
        # from select_values to a config_setting label.
        key = ":".join(select_values)
        if key == "default":
            key = "//conditions:default"
        content_str += f'{indent_str}"{key}": {_emit_attribute_values(value_indent, attribute_values)},\n'
    return content_str


def _emit_conditionals(indent: int, node: ConvertAttrNode) -> str:
    content_str = ""
    select_nodes = node.get_select_nodes()
    if not select_nodes:
        return content_str

    # Bazel's select() is a dictionary, so we can't just add them up like in Soong
    # This implementation is a simplification and might need to be more sophisticated
    # for complex cases. We take the first select node.
    select_node = select_nodes[0]

    content_str += " select({\n"
    content_str += _emit_select_values(indent, select_node)
    content_str += " " * indent + "})"
    return content_str


def _emit_python_aliases(python_deps: T.Set[str]) -> str:
    content = ""
    for dep in sorted(list(python_deps)):
        content += "alias(\n"
        content += f'    name = "{dep}",\n'
        content += f'    actual = "@meson_python_deps//{dep}",\n'
        content += '    visibility = ["//visibility:public"],\n'
        content += ")\n\n"
    return content


class BazelModuleEmitter:
    """Emits a Bazel module definition."""

    def __init__(self, target: ConvertTarget):
        self.target = target

    def emit(self) -> str:
        content = "\n\n"
        content += f"{self.target.module_type}(\n"
        content += self.emit_single_attributes()
        content += self.emit_attribute_nodes()
        if isinstance(self.target, ConvertCustomTarget):
            content += f'    cmd = "{getattr(self.target, "cmd", "")}",\n'
        content += ")"
        return content

    def emit_single_attributes(self) -> str:
        content_str = ""
        attr_indent = COMMON_INDENT * " "
        for attr, value in self.target.single_attributes.items():
            attr_name = BAZEL_ATTR_MAP.get(attr)
            if attr_name:
                content_str += f"{attr_indent}{attr_name} = {value},\n"
        return content_str

    def emit_attribute_nodes(self) -> str:
        attr_indent = COMMON_INDENT * " "
        content_str = ""
        for attr, node in self.target.attribute_nodes.items():
            if node.empty():
                continue

            attr_name = BAZEL_ATTR_MAP.get(attr)
            if not attr_name:
                continue

            content_str += f"{attr_indent}{attr_name} = "
            common_values = list(node.common_values)
            common_values.sort()

            if node.common_values:
                content_str += _emit_attribute_values(COMMON_INDENT, common_values)

            if node.select_nodes:
                if node.common_values:
                    content_str += " + "
                content_str += _emit_conditionals(COMMON_INDENT, node)
            content_str += ",\n"
        return content_str


def _get_target_sort_key(t: ConvertTarget) -> T.Tuple[int, str]:
    type_map = {
        ConvertFileGroup: 0,
        ConvertPythonTarget: 1,
        ConvertCustomTarget: 2,
        ConvertIncludeDirectory: 3,
        ConvertFlag: 4,
        ConvertStaticLibrary: 5,
        ConvertSharedLibrary: 6,
    }
    priority = type_map.get(type(t), 7)
    return (priority, t.name)


class BazelEmitter(CommonEmitter):
    """Emits the Bazel build files."""

    def emit(self, state_tracker: ConvertStateTracker) -> None:
        copyright_info = state_tracker.project_config.copyright.copy()
        copyright_info.setdefault("year", datetime.date.today().year)
        copyright_string = COPYRIGHT_HEADER_TEMPLATE.format(year=copyright_info["year"])

        license_declaration = ""
        root_license_name = ""
        if "license_name" in copyright_info:
            root_license_name = copyright_info["license_name"]
            license_kinds = "\n".join(
                [
                    f'        "@rules_license//licenses/spdx:{lic}",'
                    for lic in copyright_info.get("licenses", [])
                ]
            )
            license_declaration = ROOT_LICENSE_TEMPLATE.format(
                license_name=root_license_name,
                license_kinds=license_kinds,
            )

        python_deps: T.Set[str] = set()
        for t in state_tracker.targets.values():
            if isinstance(t, ConvertPythonTarget):
                node = t.attribute_nodes.get(ConvertAttr.BAZEL_DEPS)
                if node:
                    for val in node.common_values:
                        if val.startswith("//:"):
                            python_deps.add(val[3:])

        self._emit_module_bazel(state_tracker, copyright_string, bool(python_deps))
        if python_deps:
            self._emit_requirements_txt(python_deps)

        targets_by_subdir: T.DefaultDict[str, T.List[ConvertTarget]] = defaultdict(list)
        for t in state_tracker.targets.values():
            targets_by_subdir[t.subdir].append(t)

        for subdir, targets in targets_by_subdir.items():
            targets.sort(key=_get_target_sort_key)
            is_root = not subdir
            content = copyright_string
            content += self._emit_bazel_load_statements(
                targets, is_root, bool(root_license_name)
            )

            if root_license_name:
                if is_root:
                    content += license_declaration + "\n"
                content += (
                    LICENSE_BLOCK_TEMPLATE.format(root_license_name=root_license_name)
                    + "\n"
                )

            if is_root and python_deps:
                content += _emit_python_aliases(python_deps)

            for target in targets:
                module = BazelModuleEmitter(target)
                content += module.emit()

            content += "\n"
            output_path = (
                os.path.join(self.output_dir, subdir) if subdir else self.output_dir
            )
            os.makedirs(output_path, exist_ok=True)
            with open(
                os.path.join(output_path, "BUILD.bazel"), "w", encoding="utf-8"
            ) as f:
                f.write(content)

    def _emit_bazel_load_statements(self, targets: T.List[ConvertTarget], is_root: bool, has_license: bool) -> str:
        loads = []
        if is_root and has_license:
            loads.append(LICENSE_LOAD_TEMPLATE)
        if any(isinstance(t, ConvertPythonTarget) for t in targets):
            loads.append(PYTHON_LOAD_TEMPLATE)
        if any(t.module_type == "cc_library" for t in targets):
            loads.append(CC_LIBRARY_LOAD_TEMPLATE)

        if not loads:
            return ""

        return "\n".join(loads) + "\n\n"

    def _emit_requirements_txt(self, python_deps: T.Set[str]) -> None:
        content = ""
        # We don't have version information, so we just list the names
        # In a real scenario, we might want to get this from project_config
        for dep in sorted(list(python_deps)):
            content += f"{dep}\n"

        bazel_dir = os.path.join(self.output_dir, "bazel")
        os.makedirs(bazel_dir, exist_ok=True)
        with open(os.path.join(bazel_dir, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write(content)
        # Ensure bazel/ is a package
        with open(os.path.join(bazel_dir, "BUILD.bazel"), "w", encoding="utf-8") as f:
            f.write("")

    def _emit_module_bazel(self, state_tracker: ConvertStateTracker, copyright_string: str, has_python: bool) -> None:
        backend = T.cast(BazelBackend, state_tracker.backend)
        http_archives = []
        for dep in sorted(list(backend.external_deps), key=lambda x: x.repo):
            http_archives.append(GENERAL_HTTP_ARCHIVE_TEMPLATE.format(
                name=dep.repo,
                url=dep.source_url,
                sha256=dep.source_hash or "",
            ))

        python_setup = ""
        if has_python:
            python_setup = PYTHON_MODULE_SETUP_TEMPLATE

        toolchains_with_wrap = [tc for tc in state_tracker.all_toolchains if tc.wrap]
        if not toolchains_with_wrap:
            # Still emit MODULE.bazel even if no toolchains
            module_content = copyright_string + "\n"
            module_content += BAZEL_MODULE_TEMPLATE.format(
                project_name=state_tracker.project_config.project_name or "meson_project",
                http_archives="\n".join(http_archives),
                register_toolchains="",
                python_setup=python_setup
            )
            with open(os.path.join(self.output_dir, "MODULE.bazel"), "w", encoding="utf-8") as f:
                f.write(module_content)
            return

        register_toolchains = []
        toolchains_build_content = copyright_string + "\n"
        toolchains_build_content += 'filegroup(name = "empty")\n\n'
        platforms_build_content = copyright_string + "\n"

        toolchains_dir = os.path.join(self.output_dir, "bazel", "toolchains")
        platforms_dir = os.path.join(self.output_dir, "bazel", "platforms")
        os.makedirs(toolchains_dir, exist_ok=True)
        os.makedirs(platforms_dir, exist_ok=True)

        for tc in toolchains_with_wrap:
            wrap = tc.wrap
            name = tc.name
            http_archives.append(HTTP_ARCHIVE_TEMPLATE.format(
                name=name,
                url=wrap.url,
                sha256=wrap.sha256 or "",
                file_type="zip",
            ))
            register_toolchains.append(f'register_toolchains("//bazel/toolchains:{name}_toolchain")')
            binaries = wrap.binaries
            # Extract all available tools for tool_paths
            tool_mapping = [
                ("gcc", ["ccc", "gcc", "cc"]),
                ("cpp", ["cpp"]),
                ("ld", ["ld"]),
                ("ar", ["ar"]),
                ("nm", ["nm"]),
                ("objcopy", ["objcopy"]),
                ("objdump", ["objdump"]),
                ("gcov", ["gcov"]),
                ("strip", ["strip"]),
                ("as", ["as"]),
            ]

            tool_paths_items = []
            for bazel_name, toml_names in tool_mapping:
                for toml_name in toml_names:
                    if toml_name in binaries:
                        tool_paths_items.append(f'"{bazel_name}": "@{name}//:{binaries[toml_name]}"')
                        break

            tool_paths_str = ",\n        ".join(tool_paths_items)

            with open(os.path.join(toolchains_dir, f"{name}_compiler.BUILD"), "w", encoding="utf-8") as f:
                f.write(COMPILER_MAPPING_TEMPLATE)

            machine_info = tc.machine_info[MachineChoice.HOST]
            toolchains_build_content += TOOLCHAINS_BUILD_TEMPLATE.format(
                name=name,
                cpu=machine_info.cpu_family,
                os=machine_info.system,
                tool_paths=tool_paths_str,
            )
            platforms_build_content += PLATFORMS_BUILD_TEMPLATE.format(
                name=name,
                cpu=machine_info.cpu_family,
                os=machine_info.system,
            )

        module_content = copyright_string + "\n"
        module_content += BAZEL_MODULE_TEMPLATE.format(
            project_name=state_tracker.project_config.project_name or "meson_project",
            http_archives="\n".join(http_archives),
            register_toolchains="\n".join(register_toolchains),
            python_setup=python_setup
        )
        with open(os.path.join(self.output_dir, "MODULE.bazel"), "w", encoding="utf-8") as f:
            f.write(module_content)

        with open(os.path.join(toolchains_dir, "BUILD.bazel"), "w", encoding="utf-8") as f:
            f.write(toolchains_build_content)

        with open(os.path.join(platforms_dir, "BUILD.bazel"), "w", encoding="utf-8") as f:
            f.write(platforms_build_content)
