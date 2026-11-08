from whitenoise.storage import CompressedManifestStaticFilesStorage


class UMISStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Content-hashed filenames, tolerating missing source-map references.

    Vendor bundles (bootstrap, tom-select) ship CSS/JS that ends with a
    `sourceMappingURL=...` comment pointing at a .map we do not ship.
    Django's HashedFilesMixin.url_converter calls hashed_name() on the
    referenced file, which raises ValueError if the file is absent from
    the slug; WhiteNoise then rephrases that as MissingFileError and
    collectstatic aborts the build.

    Wrap the converter so that one ValueError returns the original match
    verbatim. The dangling sourceMappingURL stays in the output and the
    browser 404s on the .map, but the build passes.
    """

    manifest_strict = False

    def url_converter(self, name, hashed_files, template=None):
        converter = super().url_converter(name, hashed_files, template)

        def safe_converter(matchobj):
            try:
                return converter(matchobj)
            except ValueError:
                return matchobj.group(0)

        return safe_converter
