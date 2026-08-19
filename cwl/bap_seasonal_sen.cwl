cwlVersion: v1.0
$graph:
- class: Workflow
  id: main
  label: "Combined BAP + Seasonal Sen CWL Wrapper Workflow"
  doc: >
    Runs the BAP algorithm to build the monthly composites and manifest, then
    feeds that output directly into the seasonal-sen algorithm as its
    "bap_sen" input. This chains the two steps within a single CWL run, so
    the CWL engine stages the BAP output between steps instead of it being
    manually uploaded to and re-downloaded from S3 between two separate
    workflow runs.

  inputs:
    bap_parameters:
      type: File
      label: "BAP parameters"
      doc: "A JSON file of parameters to pass to the BAP algorithm."
    seasonal_sen_parameters:
      type: File
      label: "seasonal-sen parameters"
      doc: >
        A JSON file of parameters to pass to the seasonal-sen algorithm.
        Its bap_composite_dir/bap_manifest_file entries must point at
        /hLnUAz/target/output, matching where this workflow mounts the BAP
        composites produced by the bap_step.
    cdse_client_id:
      type: string
      label: "Copernicus Dataspace Client ID"
    cdse_client_secret:
      type: string
      label: "Copernicus Dataspace Client Secret"
    bap_run_name:
      type: string
      label: "BAP Run Name"
    seasonal_sen_run_name:
      type: string
      label: "Seasonal Sen Run Name"

  outputs:
    bap_results:
      type: Directory
      outputSource: bap_step/results
    seasonal_sen_results:
      type: Directory
      outputSource: seasonal_sen_step/results

  steps:
    bap_step:
      run: "#bap_runner"
      in:
        parameters: bap_parameters
        cdse_client_id: cdse_client_id
        cdse_client_secret: cdse_client_secret
        run_name: bap_run_name
      out: [results, bap_composites]

    seasonal_sen_step:
      run: "#seasonal_sen_runner"
      in:
        parameters: seasonal_sen_parameters
        cdse_client_id: cdse_client_id
        cdse_client_secret: cdse_client_secret
        run_name: seasonal_sen_run_name
        bap_sen: bap_step/bap_composites
      out: [results]

- class: CommandLineTool
  id: bap_runner
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

- class: CommandLineTool
  id: seasonal_sen_runner
  baseCommand: ["/bin/sh", "-c"]

  requirements:
    DockerRequirement:
      dockerPull: ghcr.io/people-ecco/hatfield-seasonal_sen:latest
    InitialWorkDirRequirement:
      listing:
        - entryname: /hLnUAz/target/output
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
