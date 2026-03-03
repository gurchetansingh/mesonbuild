#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Meson Development Team

from __future__ import annotations
import typing as T

from mesonbuild import mlog
from mesonbuild.mesonlib import MachineChoice
from mesonbuild.convert.abstract.abstract_toolchain import (
    AbstractToolchainInfo,
)

from mesonbuild.convert.common_defs import (
    SelectInstance,
    SelectId,
    SelectKind,
)

from mesonbuild.convert.instance.convert_instance_utils import (
    ConvertDep,
    ConvertSrc,
    ConvertInstanceFlag,
    ConvertInstanceIncludeDirectory,
    ConvertInstanceFileGroup,
)
from mesonbuild.convert.build_systems.common import (
    ConvertAttr,
    ConvertBackend,
    ConvertStateTracker,
    ConvertFileGroup,
    ConvertIncludeDirectory,
    ConvertPythonTarget,
    ConvertFlag,
    ConvertBuildTarget,
    ConvertCustomTarget,
)
from mesonbuild.convert.instance.convert_instance_build_target import (
    ConvertInstanceStaticLibrary,
    ConvertInstanceSharedLibrary,
)
from mesonbuild.convert.instance.convert_instance_custom_target import (
    ConvertInstanceCustomTarget,
    ConvertInstancePythonTarget,
    ConvertCustomTargetCmdPart,
    ConvertCustomTargetCmdPartType,
)

def _get_bazel_targets(convert_deps: T.List[ConvertDep], backend: BazelBackend) -> T.List[str]:
    bazel_targets: T.List[str] = []
    for dep in convert_deps:
        if dep.repo:
            if dep.subdir:
                bazel_target = f"@{dep.repo}//{dep.subdir}:{dep.target}"
            else:
                bazel_target = f"@{dep.repo}//:{dep.target}"
            if dep.source_url:
                backend.external_deps.add(dep)
        else:
            bazel_target = f"//{dep.subdir}:{dep.target}"

        bazel_targets.append(bazel_target)

    return bazel_targets

def _get_bazel_sources(convert_srcs: T.List[ConvertSrc], backend: BazelBackend) -> T.List[str]:
    bazel_srcs: T.List[str] = []
    for src in convert_srcs:
        if src.target_dep:
            bazel_srcs.extend(_get_bazel_targets([src.target_dep], backend))
        else:
            bazel_srcs.append(src.source)

    return bazel_srcs


class BazelBackend(ConvertBackend):
    """Bazel backend for build system conversion."""
    def __init__(self) -> None:
        self.converted_custom_targets: T.Dict[str, T.Tuple[str, str]] = {}
        self.external_deps: T.Set[ConvertDep] = set()

    def get_os_info(
        self, toolchain: AbstractToolchainInfo, choice: MachineChoice
    ) -> SelectInstance:
        machine_info = toolchain.machine_info[choice]
        os_string = machine_info.system
        os_select = SelectInstance(SelectId(SelectKind.OS, "", "os"), os_string)
        return os_select

    def get_arch_info(
        self, toolchain: AbstractToolchainInfo, choice: MachineChoice
    ) -> SelectInstance:
        machine_info = toolchain.machine_info[choice]
        select_id = SelectId(SelectKind.ARCH, "", "arch")
        arch_select = SelectInstance(select_id, machine_info.cpu_family)
        return arch_select

    def add_python_config(
        self, target: ConvertPythonTarget, instance: ConvertInstancePythonTarget
    ) -> None:
        target.module_type = "py_binary"
        bazel_main = _get_bazel_sources([instance.main], self)[0]
        target.single_attributes[ConvertAttr.PYTHON_MAIN] = f'"{bazel_main}"'
        target.get_attribute_node(ConvertAttr.SRCS).add_common_values(_get_bazel_sources(instance.srcs, self))
        target.get_attribute_node(ConvertAttr.BAZEL_DEPS).add_common_values(
            [f"//:{lib}" for lib in instance.libs]
        )

    def add_flag_config(
        self,
        target: ConvertFlag,
        instance: ConvertInstanceFlag,
        toolchain: AbstractToolchainInfo,
        custom_instances: T.Set[SelectInstance],
    ) -> None:
        target.module_type = "cc_library"
        os_select = self.get_os_info(toolchain, MachineChoice.HOST)
        arch_select = self.get_arch_info(toolchain, MachineChoice.HOST)
        label = {arch_select, os_select} | custom_instances

        target.get_attribute_node(ConvertAttr.BAZEL_DEFINES).add_conditional_values(
            label, instance.compile_args
        )
        if instance.link_args:
            target.get_attribute_node(ConvertAttr.LDFLAGS).add_conditional_values(
                label, instance.link_args
            )

    def add_include_dir_config(
        self,
        target: ConvertIncludeDirectory,
        instance: ConvertInstanceIncludeDirectory,
        toolchain: AbstractToolchainInfo,
        custom_instances: T.Set[SelectInstance],
    ) -> None:
        target.module_type = "cc_library"
        os_select = self.get_os_info(toolchain, MachineChoice.HOST)
        arch_select = self.get_arch_info(toolchain, MachineChoice.HOST)
        label = {arch_select, os_select} | custom_instances
        target.get_attribute_node(ConvertAttr.INCLUDES).add_conditional_values(
            label, list(instance.paths)
        )

    def add_file_group_config(
        self, target: ConvertFileGroup, instance: ConvertInstanceFileGroup
    ) -> None:
        target.module_type = "filegroup"
        target.get_attribute_node(ConvertAttr.SRCS).add_common_values(instance.srcs)

    def _get_custom_target_cmd(
        self, convert_instance_cmds: T.List[ConvertCustomTargetCmdPart]
    ) -> str:
        final_cmd = []
        for p in convert_instance_cmds:
            if isinstance(p, ConvertCustomTargetCmdPart):
                if p.cmd_type == ConvertCustomTargetCmdPartType.TOOL:
                    if p.src:
                        bazel_src = _get_bazel_sources([p.src], self)[0]
                        final_cmd.append(f"$(location {bazel_src})")
                    else:
                        final_cmd.append(f"$(location {p.cmd})")
                elif p.cmd_type == ConvertCustomTargetCmdPartType.PYTHON_BINARY:
                    if p.src:
                        bazel_src = _get_bazel_sources([p.src], self)[0]
                        final_cmd.append(f"$(location {bazel_src})")
                    else:
                        final_cmd.append(f"$(location {p.cmd})")
                elif p.cmd_type == ConvertCustomTargetCmdPartType.INPUT:
                    if p.src:
                        bazel_src = _get_bazel_sources([p.src], self)[0]
                        final_cmd.append(f"$(location {bazel_src})")
                    else:
                        final_cmd.append(f"$(location {p.cmd})")
                elif p.cmd_type == ConvertCustomTargetCmdPartType.OUTPUT:
                    final_cmd.append(f"$(location {p.cmd})")
                elif p.cmd_type == ConvertCustomTargetCmdPartType.STRING:
                    final_cmd.append(p.cmd)
        return " ".join(final_cmd)

    def add_custom_target(
        self, state_tracker: ConvertStateTracker, ct: ConvertInstanceCustomTarget
    ) -> None:
        if ct.name not in state_tracker.targets:
            state_tracker.targets[ct.name] = ConvertCustomTarget(ct.name, ct.subdir, ct)

        target = T.cast(ConvertCustomTarget, state_tracker.targets[ct.name])
        if target.instance != ct:
            state_tracker.targets.pop(ct.name)
            mlog.warning("Dropped custom target that differed across configs")
            return

        target.module_type = "genrule"
        # We store the cmd on the target object itself for the emitter
        out = ct.generated_headers + ct.generated_sources
        target.get_attribute_node(ConvertAttr.OUT).add_common_values(out)
        target.get_attribute_node(ConvertAttr.SRCS).add_common_values(_get_bazel_sources(ct.srcs, self))
        target.get_attribute_node(ConvertAttr.TOOLS).add_common_values(_get_bazel_sources(ct.tools, self))
        target.cmd = self._get_custom_target_cmd(ct.convert_instance_cmds)

    def add_build_target_config(
        self,
        target: ConvertBuildTarget,
        instance: T.Union[ConvertInstanceStaticLibrary, ConvertInstanceSharedLibrary],
        toolchain: AbstractToolchainInfo,
        custom_instances: T.Set[SelectInstance],
    ) -> None:
        target.module_type = "cc_library"
        os_select = self.get_os_info(toolchain, instance.machine_choice)
        arch_select = self.get_arch_info(toolchain, instance.machine_choice)
        label = {arch_select, os_select} | custom_instances

        all_deps = (
            list(instance.generated_flags)
            + list(instance.generated_include_dirs)
            + _get_bazel_targets(instance.header_libs, self)
            + _get_bazel_targets(instance.static_libs, self)
            + _get_bazel_targets(instance.shared_libs, self)
            + _get_bazel_targets(instance.whole_static_libs, self)
            + _get_bazel_targets(instance.generated_headers, self)
            + _get_bazel_targets(instance.generated_sources, self)
        )

        target.get_attribute_node(ConvertAttr.SRCS).add_conditional_values(
            label, _get_bazel_sources(instance.srcs, self)
        )
        target.get_attribute_node(ConvertAttr.BAZEL_DEPS).add_conditional_values(
            label, all_deps
        )
