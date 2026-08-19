cwlVersion: v1.0
$graph:
- class: Workflow
  id: main
  label: "Standalone seasonal-sen CWL Wrapper Workflow"
  doc: "A workflow to run the seasonal-sen algorithm using the standalone cwl_wrapper."

  inputs:
    parameters:
      type: File
      label: "seasonal-sen parameters"
      doc: "A JSON file of parameters to pass to the seasonal-sen algorithm."
    cdse_client_id:
      type: string
      label: "Copernicus Dataspace Client ID"
    cdse_client_secret:
      type: string
      label: "Copernicus Dataspace Client Secret"
    run_name:
      type: string
      label: "Run Name"
    bap_sen:
      type: Directory
      label: "BAP SEN Input Data"
      doc: "Local directory mounted to /bap_composites inside the container."

  outputs:
    results:
      type: Directory
      outputSource: cwl_wrapper_step/results

  steps:
    cwl_wrapper_step:
      run: "#cwl_wrapper_runner"
      in:
        parameters: parameters
        cdse_client_id: cdse_client_id
        cdse_client_secret: cdse_client_secret
        run_name: run_name
        bap_sen: bap_sen
      out: [results]

- class: CommandLineTool
  id: cwl_wrapper_runner
  baseCommand: ["/bin/sh", "-c"]

  requirements:
    DockerRequirement:
      dockerPull: ghcr.io/people-ecco/hatfield-seasonal_sen:latest
    InitialWorkDirRequirement:
      listing:
        - entryname: /bap_composites
          entry: $(inputs.bap_sen)
          writable: false
    EnvVarRequirement:
      envDef:
        - envName: PYTHONPATH
          envValue: "/app"
        - envName: ALGORITHM_BASE
          envValue: "seasonal_sen.main"
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
    bap_sen:
      type: Directory

  outputs:
    results:
      type: Directory
      outputBinding:
        glob: $(inputs.run_name)
