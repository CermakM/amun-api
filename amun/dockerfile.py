#!/usr/bin/env python3
# Amun
# Copyright(C) 2018, 2019 Fridolin Pokorny
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Utility functions for Amun API."""

import json
import logging
import os

import toml
import requests

from .exceptions import ScriptObtainingError

_LOGGER = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENTRYPOINT_PY = os.path.join(_HERE, 'inspect.py')
# Make sure pip and Pipenv trust the AICoE index.
_PIP_CONF = """
[global]
trusted-host = tensorflow.pypi.thoth-station.ninja
"""


def _determine_update_string() -> str:
    """Determine how to update the system to the latest version based on binaries present in the image."""
    return "RUN dnf update || yum update || apt-cache update"


def _determine_installer_string() -> str:
    """Determine how to install packages on the system regardless of package manager."""
    return """RUN { { [ -f '/usr/bin/dnf' ] && INSTALL_CMD='dnf install -y'; } || \
{ [ -f '/usr/bin/yum' ] && INSTALL_CMD='yum install -y'; } || \
{ INSTALL_CMD='apt-get install'; } };\\\n eval $INSTALL_CMD  """


def _obtain_script(script: str) -> str:
    """Obtain script if it was specified by an URL, if script was provided inline, return it."""
    if script.startswith(('https://', 'http://')):
        # Download script from remote if needed.
        response = requests.get(script)
        try:
            response.raise_for_status()
        except requests.HTTPError:
            raise ScriptObtainingError(
                f"Failed to obtain script from {script} (HTTP status: {response.status_code})"
            )

        return response.text

    return script


def _write_file_string(content: str, path: str) -> str:
    """Generate Dockerfile instruction that writes down the file content on the given path."""
    # TODO: accept a list of files so we generate only one layer for all files
    # TODO: escape content
    # TODO: handle it in nice way so we can see it nicely in OpenShift's configuration
    content = content.replace('"', '\\"').replace('\n', '\\n\\\n')
    path = path.replace('"', '\"')
    return f'RUN echo -e "{content}" >"{path}"\n'


def create_dockerfile(specification: dict) -> tuple:
    """Create a Dockerfile based on software stack specification."""
    script_present = False
    dockerfile = "FROM " + specification['base'] + "\n\n"

    dockerfile += "USER root\n\n"

    env_str = ''
    for environ in specification.get("environment", []):
        env_str += f"{environ['name']}={environ['value']} "

    if env_str:
        dockerfile += f"ENV {env_str}\n\n"

    # Updating the base has to be turned on explicitly.
    if specification.get('update', False):
        dockerfile += _determine_update_string()

    if specification.get('packages'):
        dockerfile += _determine_installer_string() + " ".join(specification['packages']) + '\n\n'

    if specification.get('python_packages'):
        dockerfile += "RUN pip3 install " + " ".join(specification["python_packages"]) + '\n\n'

    for file_spec in specification.get('files', []):
        path = file_spec['path']
        content = file_spec['content']
        # This trick helps so that env variables are not expanded.
        dockerfile += _write_file_string(content, path)

    # Create workdir only if needed.
    if 'python' in specification or 'script' in specification:
        dockerfile += f'RUN mkdir -p /home/amun && chmod -R 777 /home/amun\n'

    if 'python' in specification:
        pipfile_content = toml.dumps(specification['python']['requirements'])
        pipfile_lock_content = json.dumps(specification['python']['requirements_locked'], sort_keys=True, indent=4)
        dockerfile += _write_file_string(pipfile_content, '/home/amun/Pipfile')
        dockerfile += _write_file_string(pipfile_lock_content, '/home/amun/Pipfile.lock')
        dockerfile += _write_file_string(_PIP_CONF, "/etc/pip.conf")
        dockerfile += 'RUN cd /home/amun && pipenv install --deploy\n'

    if 'script' in specification:
        script_present = True
        content = _obtain_script(specification['script'])
        dockerfile += _write_file_string(content, '/home/amun/script')
        with open(_ENTRYPOINT_PY, 'r') as entrypoint_file:
            dockerfile += _write_file_string(
                entrypoint_file.read(),
                '/home/amun/entrypoint'
            )

        dockerfile += 'RUN chmod a+x /home/amun/script /home/amun/entrypoint && ' \
            'touch /home/amun/script.stderr /home/amun/script.stdout && ' \
            'chmod 777 /home/amun/script.stderr /home/amun/script.stdout\n'
        dockerfile += 'CMD ["/home/amun/entrypoint"]\n'

    # An arbitrary user.
    dockerfile += "USER 1042\n"
    dockerfile += "WORKDIR /home/amun"

    return dockerfile, script_present
