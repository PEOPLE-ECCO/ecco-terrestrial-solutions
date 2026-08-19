cwlVersion: v1.0
$graph:
- class: Workflow
  id: main
  label: "Standalone BAP CWL Wrapper Workflow"
  doc: "A workflow to run the BAP algorithm using the standalone cwl_wrapper."

  inputs:
    parameters:
      type: File
      label: "BAP parameters"
      doc: "A JSON file of parameters to pass to the BAP algorithm."
    cdse_client_id:
      type: string
      label: "Copernicus Dataspace Client ID"
    cdse_client_secret:
      type: string
      label: "Copernicus Dataspace Client Secret"
    run_name:
      type: string
      label: "Run Name"

  outputs:
    results:
      type: Directory
      outputSource: cwl_wrapper_step/results
    bap_composites:
      type: Directory
      label: "BAP Composites"
      doc: >
        The BAP output directory (manifest + composite rasters), without the
        run_name wrapper folder. Matches the directory shape that
        seasonal_sen.cwl expects for its "bap_sen" input, so this output can
        be wired directly into a downstream step instead of staging it
        through S3.
      outputSource: cwl_wrapper_step/bap_composites

  steps:
    cwl_wrapper_step:
      run: "#cwl_wrapper_runner"
      in:
        parameters: parameters
        cdse_client_id: cdse_client_id
        cdse_client_secret: cdse_client_secret
        run_name: run_name
      out: [results, bap_composites]

- class: CommandLineTool
  id: cwl_wrapper_runner
  baseCommand: ["/bin/sh", "-c"]

  requirements:
    DockerRequirement:
      dockerPull: ghcr.io/people-ecco/hatfield-bap:latest
    EnvVarRequirement:
      envDef:
        - envName: PYTHONPATH
          envValue: "/app"
        - envName: ALGORITHM_BASE
          envValue: "bap.main"
        - envName: PARAMETERS_FILE
          envValue: $(inputs.parameters.path)
        - envName: CDSE_CLIENT_ID
          envValue: $(inputs.cdse_client_id)
        - envName: CDSE_CLIENT_SECRET
          envValue: $(inputs.cdse_client_secret)
        - envName: RUN_NAME
          envValue: $(inputs.run_name)
        - envName: OUTPUT_DIR
          envValue: $(runtime.outdir)/$(inputs.run_name)/output
  arguments:
    - valueFrom: "python -u /app/cwl_wrapper.py"

  inputs:
    parameters:
      type: File
    cdse_client_id:
      type: string
    cdse_client_secret:
      type: string
    run_name:
      type: string

  outputs:
    results:
      type: Directory
      outputBinding:
        glob: $(inputs.run_name)
    bap_composites:
      type: Directory
      outputBinding:
        glob: $(inputs.run_name)/output
