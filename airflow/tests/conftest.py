import os
import tempfile

# A throwaway Airflow home: tests never read nor write the developer's ~/airflow.
os.environ["AIRFLOW_HOME"] = tempfile.mkdtemp(prefix="airflow-home-")
os.environ.setdefault("AIRFLOW__CORE__LOAD_EXAMPLES", "False")
