/* Optional CPython accelerator. The GIL stays held: input lists contain Python
 * numbers and must not be mutated while their elements are being inspected. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>

static int number(PyObject *value, double *result)
{
    if (PyFloat_Check(value)) {
        *result = PyFloat_AS_DOUBLE(value);
        return 1;
    }
    if (PyLong_Check(value)) {
        *result = PyLong_AsDouble(value);
        return !PyErr_Occurred();
    }
    PyErr_SetString(PyExc_TypeError, "Embedding coordinates must be floats or integers");
    return 0;
}

static PyObject *cosine(PyObject *self, PyObject *args)
{
    (void)self;
    PyObject *a, *b;
    if (!PyArg_ParseTuple(args, "OO:cosine", &a, &b)) {
        return NULL;
    }
    PyObject *left = PySequence_Fast(a, "Expected an embedding sequence");
    if (!left) {
        return NULL;
    }
    PyObject *right = PySequence_Fast(b, "Expected an embedding sequence");
    if (!right) {
        Py_DECREF(left);
        return NULL;
    }
    Py_ssize_t count = PySequence_Fast_GET_SIZE(left);
    double dot = 0.0, norm_a = 0.0, norm_b = 0.0;
    if (count != PySequence_Fast_GET_SIZE(right)) {
        Py_DECREF(left);
        Py_DECREF(right);
        return PyFloat_FromDouble(0.0);
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        double x, y;
        if (!number(PySequence_Fast_GET_ITEM(left, i), &x) ||
            !number(PySequence_Fast_GET_ITEM(right, i), &y)) {
            Py_DECREF(left);
            Py_DECREF(right);
            return NULL;
        }
        dot += x * y;
        norm_a += x * x;
        norm_b += y * y;
    }
    Py_DECREF(left);
    Py_DECREF(right);
    double divisor = sqrt(norm_a * norm_b);
    return PyFloat_FromDouble(divisor != 0.0 ? dot / divisor : 0.0);
}

static PyMethodDef methods[] = {
    {"cosine", cosine, METH_VARARGS, "Cosine similarity for numeric embedding sequences."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_retrieval_native",
    .m_doc = "Optional C accelerator for Bara document retrieval.",
    .m_size = -1,
    .m_methods = methods
};

PyMODINIT_FUNC PyInit__retrieval_native(void)
{
    return PyModule_Create(&module);
}
