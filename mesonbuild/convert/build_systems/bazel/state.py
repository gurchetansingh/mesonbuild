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


class BazelBackend(ConvertBackend):
    """Bazel backend for build system conversion."""

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
        target.single_attributes[ConvertAttr.PYTHON_MAIN] = f'"{instance.main}"'
        target.get_attribute_node(ConvertAttr.SRCS).add_common_values(instance.srcs)
        target.get_attribute_node(ConvertAttr.BAZEL_DEPS).add_common_values(
            instance.libs
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
                    final_cmd.append(f"$(location {p.cmd})")
                elif p.cmd_type == ConvertCustomTargetCmdPartType.PYTHON_BINARY:
                    final_cmd.append(f"$(location {p.cmd})")
                elif p.cmd_type == ConvertCustomTargetCmdPartType.INPUT:
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
        target.get_attribute_node(ConvertAttr.SRCS).add_common_values(ct.srcs)
        target.get_attribute_node(ConvertAttr.TOOLS).add_common_values(ct.tools)
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
            + instance.header_libs
            + instance.static_libs
            + instance.shared_libs
            + instance.whole_static_libs
            + instance.generated_headers
            + instance.generated_sources
        )

        target.get_attribute_node(ConvertAttr.SRCS).add_conditional_values(
            label, instance.srcs
        )
        target.get_attribute_node(ConvertAttr.BAZEL_DEPS).add_conditional_values(
            label, all_deps
        )
