# Third-party dependencies

This project's code is MIT licensed; that does not relicense its dependencies.
No third-party source tree, wheel, shared library or private runtime is vendored
in this source distribution.

Runtime dependencies declared in `pyproject.toml`:

- **cryptography**: the project's upstream distribution declares Apache/BSD
  licensing. See <https://cryptography.io/en/latest/> and the license files in
  the installed distribution. Use and preserve the applicable upstream notices.
- **jsonschema**: MIT. See <https://github.com/python-jsonschema/jsonschema>.

Their dependency resolver may install additional packages (for example schema
reference/resource libraries or cryptographic bindings). Review the exact lock
and distribution notices for your target platform, especially when bundling an
executable. Python, TLS libraries, compiler/runtime libraries and dependencies
retain their respective licenses.

Build/test tools such as setuptools, wheel, build and pytest are not embedded in
the runtime package. Release verification records the versions actually used;
this document is not a substitute for a distribution-specific SBOM.

The deterministic canonical JSON encoder is derived from SEDB-RAL's canonical
encoding implementation, included under the MIT license authorized for this
public edition. It uses the named SEDB-RAL encoding contract, not RFC 8785 JCS.
Other management, signing-provider and storage boundaries in this public profile
are independently packaged and do not import another deployment or its data.
