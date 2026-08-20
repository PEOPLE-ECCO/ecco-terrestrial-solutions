cwlVersion: v1.0
$graph:
- class: Workflow
  id: main
  label: "Standalone Spectral Recovery (basin_loop) CWL Wrapper Workflow"
  doc: "A workflow to run the spectral recovery algorithm in basin_loop mode using the standalone cwl_wrapper."

  inputs:
    parameters:
      type: File
      label: "spectral-recovery parameters"
      doc: "A JSON file of parameters to pass to the spectral-recovery algorithm. execution_mode must be 'basin_loop'."
    aoi_basins_file:
      type: File
      label: "Basin AOI vector file"
      doc: "Vector file (e.g. GeoJSON/GeoPackage) with one polygon per basin. Its staged path is passed to the algorithm via AOI_BASINS_FILE, since the static parameters file cannot know it ahead of time."
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

  steps:
    cwl_wrapper_step:
      run: "#cwl_wrapper_runner"
      in:
        parameters: parameters
        aoi_basins_file: aoi_basins_file
        cdse_client_id: cdse_client_id
        cdse_client_secret: cdse_client_secret
        run_name: run_name
      out: [results]

- class: CommandLineTool
  id: cwl_wrapper_runner
  baseCommand: ["/bin/sh", "-c"]

  requirements:
    DockerRequirement:
      dockerPull: ghcr.io/people-ecco/hatfield-spectral-recovery:latest
    EnvVarRequirement:
      envDef:
        - envName: PYTHONPATH
          envValue: "/app"
        - envName: ALGORITHM_BASE
          envValue: "spectral_recovery_algo.main"
        - envName: PARAMETERS_FILE
          envValue: $(inputs.parameters.path)
        - envName: AOI_BASINS_FILE
          envValue: $(inputs.aoi_basins_file.path)
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
    aoi_basins_file:
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
