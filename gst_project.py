import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { initializeApp } from 'firebase/app';
import { 
    getAuth, 
    signInAnonymously, 
    signInWithCustomToken, 
    onAuthStateChanged,
    setPersistence,
    inMemoryPersistence
} from 'firebase/auth';
import { 
    getFirestore, 
    collection, 
    query, 
    onSnapshot, 
    addDoc, 
    doc, 
    updateDoc,
    setLogLevel
} from 'firebase/firestore';
import { 
    CheckCircle, 
    FileText, 
    DollarSign, 
    UploadCloud, 
    RefreshCw, 
    XOctagon, 
    LogOut,
    User,
    ClipboardCheck,
    AlertTriangle,
    File,
    Search,
    Eye,
    X,
    List
} from 'lucide-react';

// Enable debug logging for Firestore
setLogLevel('debug');

// --- 1. FIREBASE SETUP & CONTEXT ---
const appId = typeof __app_id !== 'undefined' ? __app_id : 'default-app-id';
// FIX: Ensured ternary operators are on a single line to prevent compilation error
const firebaseConfig = typeof __firebase_config !== 'undefined' ? JSON.parse(__firebase_config) : null;
const initialAuthToken = typeof __initial_auth_token !== 'undefined' ? __initial_auth_token : null;

let app, auth, db;

if (firebaseConfig) {
    try {
        app = initializeApp(firebaseConfig);
        auth = getAuth(app);
        db = getFirestore(app);
    } catch (e) {
        console.error("Firebase initialization failed:", e);
    }
}

// --- 2. API CONFIGURATION & HELPERS ---

const API_KEY = ""; // Canvas environment provides the API key dynamically
const GEMINI_API_URL = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key=${API_KEY}`;
const MAX_RETRIES = 3;

const InvoiceStatus = {
    PENDING: 'Pending Reconciliation',
    RECONCILED: 'Reconciled',
    MISMATCH: 'Mismatch (Review)',
    FILED: 'Filed'
};

const Views = {
    DASHBOARD: 'Dashboard',
    UPLOAD: 'Invoice Upload',
    RECONCILIATION: 'Reconciliation',
    FILING: 'GSTR-3B Filing'
};

/**
 * Converts a File object to a Base64 string for API inline data transfer.
 * @param {File} file The file object (image/pdf).
 * @returns {Promise<string>} Base64 data string.
 */
const fileToBase64 = (file) => {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            // Extracts the base64 part (after "data:mime/type;base64,")
            const base64String = reader.result.split(',')[1];
            resolve(base64String);
        };
        reader.onerror = (error) => reject(error);
        reader.readAsDataURL(file);
    });
};

/**
 * Extracts structured data from the uploaded file using the Gemini API.
 * @param {File} file The file object (image/pdf).
 * @returns {Promise<object>} Extracted and validated invoice data.
 */
const extractInvoiceData = async (file) => {
    const base64Data = await fileToBase64(file);
    const mimeType = file.type;

    const systemPrompt = "You are a specialized GST document parser. Your task is to accurately extract key financial, identifying, and **line-item** details from the provided invoice or purchase order image/PDF, specifically for Indian GST compliance. Return the extracted data STRICTLY as a JSON object matching the provided schema. Ensure 'taxableValue', 'igst', 'quantity', and 'unitPrice' are returned as numeric values. If a field is not present, return 0 for numbers and 'N/A' for strings.";

    const payload = {
        contents: [
            {
                parts: [
                    { text: "Extract the invoice details required for GST filing: invoice number, date (in YYYY-MM-DD format), supplier name, supplier GSTIN (15 characters), the total **Taxable Value** (net amount before GST), the total **IGST** (Integrated Goods and Services Tax) amount, and an **array of line items**. Each line item must include its description, quantity, and unit price." },
                    {
                        inlineData: {
                            mimeType: mimeType,
                            data: base64Data
                        }
                    }
                ]
            }
        ],
        systemInstruction: {
            parts: [{ text: systemPrompt }]
        },
        generationConfig: {
            responseMimeType: "application/json",
            responseSchema: {
                type: "OBJECT",
                properties: {
                    invoiceNumber: { type: "STRING" },
                    invoiceDate: { type: "STRING" }, // YYYY-MM-DD
                    supplierName: { type: "STRING" },
                    supplierGSTIN: { type: "STRING" },
                    taxableValue: { type: "NUMBER" },
                    igst: { type: "NUMBER" },
                    lineItems: { // NEW FIELD: Array for product details
                        type: "ARRAY",
                        items: {
                            type: "OBJECT",
                            properties: {
                                description: { type: "STRING" },
                                quantity: { type: "NUMBER" },
                                unitPrice: { type: "NUMBER" },
                            },
                            required: ["description", "quantity", "unitPrice"]
                        }
                    }
                },
                required: ["invoiceNumber", "invoiceDate", "supplierGSTIN", "taxableValue", "igst", "lineItems"]
            }
        }
    };

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        const delay = Math.pow(2, attempt) * 1000 + Math.random() * 1000;
        if (attempt > 0) await new Promise(resolve => setTimeout(resolve, delay));

        try {
            const response = await fetch(GEMINI_API_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                if (response.status === 429 && attempt < MAX_RETRIES - 1) {
                    continue; // Retry on rate limit
                }
                throw new Error(`API request failed with status: ${response.status}`);
            }

            const result = await response.json();
            const jsonText = result.candidates?.[0]?.content?.parts?.[0]?.text;
            
            if (!jsonText) {
                 throw new Error("Gemini returned no content or structured JSON.");
            }

            const parsedData = JSON.parse(jsonText);
            
            // Basic validation and type coercion
            const validatedData = {
                invoiceNumber: String(parsedData.invoiceNumber || 'N/A'),
                invoiceDate: String(parsedData.invoiceDate || new Date().toISOString().slice(0, 10)),
                supplierName: String(parsedData.supplierName || 'Unknown Supplier'),
                supplierGSTIN: String(parsedData.supplierGSTIN || 'N/A'),
                taxableValue: parseFloat(parsedData.taxableValue) || 0,
                igst: parseFloat(parsedData.igst) || 0,
                lineItems: Array.isArray(parsedData.lineItems) ? parsedData.lineItems.map(item => ({
                    description: String(item.description || 'N/A'),
                    quantity: parseFloat(item.quantity) || 0,
                    unitPrice: parseFloat(item.unitPrice) || 0,
                })) : [] // Ensure lineItems is an array
            };
            
            return validatedData;

        } catch (error) {
            if (attempt === MAX_RETRIES - 1) {
                // Log and re-throw only on final failure
                console.error("Failed to extract data after multiple retries:", error);
                throw new Error(`Failed to extract data: ${error.message}`);
            }
            // Retrying
        }
    }
};

// --- 3. REACT COMPONENTS ---

// Custom hook to handle Firebase initialization and state
const useFirebase = () => {
    const [userId, setUserId] = useState(null);
    const [isAuthReady, setIsAuthReady] = useState(false);

    useEffect(() => {
        if (!auth) return;

        const initializeAuth = async () => {
            try {
                // Ensure persistence is set
                await setPersistence(auth, inMemoryPersistence);

                if (initialAuthToken) {
                    await signInWithCustomToken(auth, initialAuthToken);
                } else {
                    await signInAnonymously(auth);
                }
            } catch (error) {
                console.error("Firebase Auth Error during sign-in:", error);
            }
        };

        const unsubscribe = onAuthStateChanged(auth, (user) => {
            if (user) {
                setUserId(user.uid);
            } else {
                setUserId(null);
            }
            setIsAuthReady(true);
        });

        if (!isAuthReady) {
            initializeAuth();
        }

        return () => unsubscribe();
    }, [isAuthReady]);

    return { db, auth, userId, isAuthReady, appId };
};

// Custom hook to fetch and listen to invoice data
const useInvoices = (db, userId, isAuthReady, appId) => {
    const [invoices, setInvoices] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!db || !userId || !isAuthReady) {
            if (isAuthReady) setLoading(false);
            return;
        }

        // Firestore private collection path for the current user
        const invoicesPath = `/artifacts/${appId}/users/${userId}/invoices`;
        const q = query(collection(db, invoicesPath));

        const unsubscribe = onSnapshot(q, (snapshot) => {
            const invoiceList = snapshot.docs.map(doc => ({
                id: doc.id,
                ...doc.data(),
            }));
            // Sort by upload time (newest first)
            invoiceList.sort((a, b) => new Date(b.uploadTime) - new Date(a.uploadTime));

            setInvoices(invoiceList);
            setLoading(false);
        }, (error) => {
            console.error("Firestore data fetch error:", error);
            setLoading(false);
        });

        return () => unsubscribe();
    }, [db, userId, isAuthReady, appId]);

    return { invoices, loading };
};

const Navbar = ({ userId, currentView, setCurrentView }) => {
    const navItems = [
        { name: Views.DASHBOARD, icon: FileText },
        { name: Views.UPLOAD, icon: UploadCloud },
        { name: Views.RECONCILIATION, icon: RefreshCw },
        { name: Views.FILING, icon: DollarSign },
    ];

    return (
        <nav className="bg-gray-800 text-white p-4 shadow-lg flex justify-between items-center">
            <div className="flex items-center space-x-6">
                <div className="text-xl font-bold tracking-wider">GST CA Portal</div>
                <div className="hidden sm:flex space-x-4">
                    {navItems.map(item => (
                        <button
                            key={item.name}
                            onClick={() => setCurrentView(item.name)}
                            className={`flex items-center px-3 py-2 rounded-lg transition-colors duration-200 ${
                                currentView === item.name 
                                    ? 'bg-blue-600 text-white shadow-md' 
                                    : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                            }`}
                        >
                            <item.icon className="w-5 h-5 mr-2" />
                            {item.name}
                        </button>
                    ))}
                </div>
            </div>
            <div className="flex items-center space-x-3">
                 <div className="text-sm text-gray-400 truncate hidden md:block">
                    <User className="w-4 h-4 inline mr-1 align-sub" /> User ID: {userId ? userId.substring(0, 8) + '...' : 'Loading'}
                </div>
                <button
                    onClick={() => auth.signOut()}
                    className="flex items-center p-2 rounded-full text-red-400 hover:bg-red-500 hover:text-white transition-colors"
                    title="Sign Out"
                >
                    <LogOut className="w-5 h-5" />
                </button>
            </div>
        </nav>
    );
};

const StatCard = ({ title, value, icon: Icon, colorClass, desc }) => (
    <div className={`bg-white p-6 rounded-xl shadow-lg border-l-4 ${colorClass}`}>
        <div className="flex justify-between items-start">
            <div>
                <p className="text-sm font-medium text-gray-500">{title}</p>
                <p className="mt-1 text-3xl font-semibold text-gray-900">{value}</p>
            </div>
            <div className={`p-3 rounded-full ${colorClass.replace('border-l-4', 'bg').replace('-600', '-100').replace('-500', '-100')} ${colorClass.replace('border-l-4', 'text')}`}>
                <Icon className="w-6 h-6" />
            </div>
        </div>
        <p className="mt-2 text-xs text-gray-400">{desc}</p>
    </div>
);

const DashboardView = ({ invoices }) => {
    const summary = useMemo(() => {
        const initialSummary = {
            totalValue: 0,
            totalITC: 0,
            pending: 0,
            reconciled: 0,
            mismatch: 0,
        };

        return invoices.reduce((acc, inv) => {
            const taxableValue = parseFloat(inv.taxableValue || 0);
            const igst = parseFloat(inv.igst || 0);

            acc.totalValue += taxableValue;
            acc.totalITC += igst;

            switch (inv.status) {
                case InvoiceStatus.PENDING:
                    acc.pending++;
                    break;
                case InvoiceStatus.RECONCILED:
                    acc.reconciled++;
                    break;
                case InvoiceStatus.MISMATCH:
                    acc.mismatch++;
                    break;
                default:
                    // Do nothing for 'Filed' status on the dashboard count
                    break;
            }
            return acc;
        }, initialSummary);
    }, [invoices]);

    const formatCurrency = (amount) => `₹ ${amount.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`;

    return (
        <div className="p-6 space-y-8">
            <h2 className="text-3xl font-bold text-gray-800">GST Filing Dashboard</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <StatCard 
                    title="Total Taxable Value (YTD)" 
                    value={formatCurrency(summary.totalValue)} 
                    icon={DollarSign}
                    colorClass="border-l-4 border-blue-600"
                    desc="Sum of all uploaded invoice values."
                />
                <StatCard 
                    title="Total Potential ITC" 
                    value={formatCurrency(summary.totalITC)} 
                    icon={CheckCircle}
                    colorClass="border-l-4 border-green-600"
                    desc="Input Tax Credit (IGST) available."
                />
                <StatCard 
                    title="Pending for Review" 
                    value={summary.pending} 
                    icon={RefreshCw}
                    colorClass="border-l-4 border-yellow-500"
                    desc="Invoices pending first-level reconciliation."
                />
                <StatCard 
                    title="Mismatched Documents" 
                    value={summary.mismatch} 
                    icon={AlertTriangle}
                    colorClass="border-l-4 border-red-500"
                    desc="Invoices requiring manual intervention."
                />
            </div>

            <div className="bg-white p-6 rounded-xl shadow-lg">
                <h3 className="text-xl font-semibold text-gray-800 mb-4 border-b pb-2">Recent Invoice Activity</h3>
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Invoice No.</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Supplier</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">IGST Value</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Date</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {invoices.slice(0, 5).map((inv) => (
                                <tr key={inv.id}>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{inv.invoiceNumber}</td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{inv.supplierName}</td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{formatCurrency(inv.igst)}</td>
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <StatusBadge status={inv.status} />
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{inv.invoiceDate}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {invoices.length === 0 && (
                    <p className="text-center py-4 text-gray-500">No invoices uploaded yet. Start by using the "Invoice Upload" tab.</p>
                )}
            </div>
        </div>
    );
};

const StatusBadge = ({ status }) => {
    let classes = "";
    let icon = null;

    switch (status) {
        case InvoiceStatus.PENDING:
            classes = "bg-yellow-100 text-yellow-800";
            icon = <AlertTriangle className="w-3 h-3 mr-1" />;
            break;
        case InvoiceStatus.RECONCILED:
            classes = "bg-green-100 text-green-800";
            icon = <CheckCircle className="w-3 h-3 mr-1" />;
            break;
        case InvoiceStatus.MISMATCH:
            classes = "bg-red-100 text-red-800";
            icon = <XOctagon className="w-3 h-3 mr-1" />;
            break;
        case InvoiceStatus.FILED:
            classes = "bg-blue-100 text-blue-800";
            icon = <ClipboardCheck className="w-3 h-3 mr-1" />;
            break;
        default:
            classes = "bg-gray-100 text-gray-800";
            icon = <File className="w-3 h-3 mr-1" />;
    }

    return (
        <span className={`inline-flex items-center px-3 py-1 text-xs font-semibold rounded-full ${classes}`}>
            {icon}
            {status}
        </span>
    );
};


const UploadView = ({ db, userId, appId }) => {
    const [file, setFile] = useState(null);
    const [status, setStatus] = useState('Select an invoice or purchase order (PDF/Image) to begin extraction.');
    const [isUploading, setIsUploading] = useState(false);
    const [extractedData, setExtractedData] = useState(null);

    const handleFileChange = (e) => {
        const selectedFile = e.target.files?.[0];
        
        if (selectedFile) {
            setFile(selectedFile); // Sets the file state, enabling the button
            setStatus(`File selected: ${selectedFile.name}. Click 'Upload & Extract' to process.`);
            setExtractedData(null);
        } else {
            setFile(null);
            setStatus('File selection cancelled. Please select a file.');
        }

        // Clear input value immediately to allow re-uploading the same file repeatedly.
        e.target.value = ''; 
    };

    const handleUploadAndExtract = useCallback(async () => {
        if (!file || !db || !userId) {
            setStatus('Please select a file first.');
            return;
        }

        setIsUploading(true);
        setStatus(`Processing ${file.name} using AI-OCR and Gemini... This may take a few seconds.`);
        setExtractedData(null);

        try {
            // --- ACTUAL GEMINI API CALL ---
            const data = await extractInvoiceData(file);

            // --- MOCK GOVERNMENT DATA FOR RECONCILIATION DEMO ---
            // To demonstrate a mismatch, we'll intentionally create government data that differs slightly
            // In a real scenario, this data would be fetched from the GSTN APIs (GSTR-2A/2B).
            const taxDifference = Math.random() < 0.5 ? -0.05 : 0.03; // +/- 5% or 3% difference
            const govtTaxableValue = data.taxableValue * (1 + taxDifference);
            const govtIgst = data.igst * (1 + taxDifference);
            
            const newInvoice = {
                ...data,
                fileName: file.name,
                uploadTime: new Date().toISOString(),
                status: (Math.abs(taxDifference) > 0.01) ? InvoiceStatus.MISMATCH : InvoiceStatus.PENDING, // Mismatch if variance > 1%
                govtData: {
                    taxableValue: parseFloat(govtTaxableValue.toFixed(2)),
                    igst: parseFloat(govtIgst.toFixed(2)),
                },
            };

            // Save to Firestore
            const invoicesPath = `/artifacts/${appId}/users/${userId}/invoices`;
            await addDoc(collection(db, invoicesPath), newInvoice);

            setExtractedData(newInvoice);
            if (newInvoice.status === InvoiceStatus.MISMATCH) {
                setStatus(`Extraction complete. Mismatch detected (${(taxDifference * 100).toFixed(1)}% variance simulated). Saved for Review.`);
            } else {
                setStatus(`Successfully extracted and saved data for ${file.name}. Ready for Reconciliation.`);
            }
            
            setFile(null); // Clear file state after successful upload

        } catch (error) {
            console.error("Upload and extraction failed:", error);
            setStatus(`Error during extraction: ${error.message}. Please check console for details.`);
        } finally {
            setIsUploading(false);
        }
    }, [file, db, userId, appId]);

    return (
        <div className="p-6 space-y-6">
            <h2 className="text-3xl font-bold text-gray-800 mb-4">Invoice / PO Upload</h2>
            
            <div className="bg-white p-8 rounded-xl shadow-lg border-2 border-dashed border-gray-300">
                <input
                    type="file"
                    id="file-upload"
                    className="hidden"
                    onChange={handleFileChange}
                    accept="image/*,application/pdf"
                />
                <label 
                    htmlFor="file-upload" 
                    className="flex flex-col items-center justify-center p-6 cursor-pointer hover:bg-gray-50 transition-colors duration-200"
                >
                    <UploadCloud className="w-12 h-12 text-blue-500 mb-2" />
                    <p className="text-lg font-semibold text-gray-700">Drag & Drop or Click to Select File</p>
                    <p className="text-sm text-gray-500">Supports PDF and common image formats</p>
                </label>
            </div>
            
            {/* Clear Visual Confirmation */}
            {file && (
                <div className="flex items-center justify-center bg-blue-50 p-3 rounded-xl border border-blue-200 shadow-md">
                    <File className="w-5 h-5 text-blue-600 mr-2" />
                    <span className="font-semibold text-blue-800">File Ready: {file.name}</span>
                </div>
            )}

            <div className="flex justify-center">
                <button
                    onClick={handleUploadAndExtract}
                    disabled={!file || isUploading}
                    className={`px-6 py-3 rounded-xl font-bold text-white transition-all duration-300 shadow-lg ${
                        !file || isUploading
                            ? 'bg-gray-400 cursor-not-allowed'
                            : 'bg-green-600 hover:bg-green-700 active:scale-95'
                    }`}
                >
                    {isUploading ? (
                        <span className="flex items-center">
                            <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                            Extracting Data...
                        </span>
                    ) : (
                        <span className="flex items-center">
                            <UploadCloud className="w-4 h-4 mr-2" />
                            Upload & Extract
                        </span>
                    )}
                </button>
            </div>

            <div className={`p-4 rounded-xl font-medium ${isUploading ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-700'}`}>
                {status}
            </div>

            {extractedData && (
                <div className="bg-green-50 p-6 rounded-xl border border-green-200 mt-4 shadow-md">
                    <h4 className="text-lg font-bold text-green-700 mb-3">Extracted Data (Gemini API)</h4>
                    <pre className="text-sm bg-green-100 p-3 rounded overflow-x-auto">
                        {JSON.stringify(extractedData, null, 2)}
                    </pre>
                    <p className="text-sm mt-3 text-gray-600">
                        *This document is now in the **Reconciliation** tab for review.
                    </p>
                </div>
            )}
        </div>
    );
};

// Component for Details Modal
const InvoiceDetailModal = ({ invoice, onClose, formatCurrency }) => {
    if (!invoice) return null;
    
    // Helper to format currency values for display
    const formatValue = (value) => {
        const num = parseFloat(value);
        return isNaN(num) ? 'N/A' : formatCurrency(num);
    }
    
    // Calculate line item total value for validation
    const calculateLineItemTotal = (item) => {
        const qty = parseFloat(item.quantity || 0);
        const price = parseFloat(item.unitPrice || 0);
        return qty * price;
    }


    const ourData = {
        'Invoice Number': invoice.invoiceNumber,
        'Invoice Date': invoice.invoiceDate,
        'Supplier Name': invoice.supplierName,
        'Supplier GSTIN': invoice.supplierGSTIN,
        'Our Taxable Value (Total)': formatValue(invoice.taxableValue),
        'Our ITC (IGST)': formatValue(invoice.igst),
        'File Name': invoice.fileName,
        'Upload Time': new Date(invoice.uploadTime).toLocaleString(),
        'Current Status': invoice.status,
    };

    const govtData = {
        'Govt. Taxable Value': formatValue(invoice.govtData?.taxableValue || 0),
        'Govt. ITC (IGST)': formatValue(invoice.govtData?.igst || 0),
    };

    return (
        <div className="fixed inset-0 bg-gray-900 bg-opacity-75 z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-xl shadow-2xl max-w-4xl w-full max-h-[95vh] overflow-y-auto transform transition-all">
                <div className="p-6 border-b flex justify-between items-center sticky top-0 bg-white z-10">
                    <h2 className="text-2xl font-bold text-gray-800">Invoice Details: {invoice.invoiceNumber}</h2>
                    <button onClick={onClose} className="text-gray-500 hover:text-gray-800 transition-colors">
                        <X className="w-6 h-6" />
                    </button>
                </div>
                
                <div className="p-6 space-y-8">
                    
                    {/* 1. Extracted Data */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <h3 className="text-xl font-semibold text-blue-600 col-span-full mb-2 border-b pb-1 flex items-center">
                            <FileText className="w-5 h-5 mr-2" /> Extracted Document Header Data
                        </h3>
                        {Object.entries(ourData).map(([key, value]) => (
                            <div key={key} className="p-3 bg-blue-50 rounded-lg">
                                <p className="text-xs font-medium text-gray-500">{key}</p>
                                <p className="text-sm font-semibold text-blue-800 break-words">{value}</p>
                            </div>
                        ))}
                    </div>

                    {/* 2. Government Data */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4 border-t">
                        <h3 className="text-xl font-semibold text-green-600 col-span-full mb-2 border-b pb-1 flex items-center">
                            <ClipboardCheck className="w-5 h-5 mr-2" /> Simulated Govt. Data (GSTR-2A/2B)
                        </h3>
                        {Object.entries(govtData).map(([key, value]) => (
                            <div key={key} className="p-3 bg-green-50 rounded-lg">
                                <p className="text-xs font-medium text-gray-500">{key}</p>
                                <p className="text-sm font-semibold text-green-800 break-words">{value}</p>
                            </div>
                        ))}
                    </div>
                    
                    {/* 3. Line Item Details (New Section - NOW IN TABLE FORMAT) */}
                    <div className="pt-4 border-t">
                        <h3 className="text-xl font-semibold text-gray-700 mb-4 border-b pb-1 flex items-center">
                            <List className="w-5 h-5 mr-2" /> Product / Line Item Details
                        </h3>
                        
                        {invoice.lineItems && invoice.lineItems.length > 0 ? (
                            <div className="overflow-x-auto">
                                <table className="min-w-full divide-y divide-gray-200">
                                    <thead className="bg-gray-50">
                                        <tr>
                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Description</th>
                                            <th className="px-4 py-2 text-center text-xs font-medium text-gray-500 uppercase">Qty</th>
                                            <th className="px-4 py-2 text-center text-xs font-medium text-gray-500 uppercase">Unit Price</th>
                                            <th className="px-4 py-2 text-center text-xs font-medium text-gray-500 uppercase">Total Value</th>
                                        </tr>
                                    </thead>
                                    <tbody className="bg-white divide-y divide-gray-200">
                                        {invoice.lineItems.map((item, index) => (
                                            <tr key={index}>
                                                <td className="px-4 py-2 whitespace-normal text-sm font-medium text-gray-900 max-w-xs">{item.description}</td>
                                                <td className="px-4 py-2 whitespace-nowrap text-sm text-center text-gray-600">{item.quantity}</td>
                                                <td className="px-4 py-2 whitespace-nowrap text-sm text-right text-gray-600">{formatValue(item.unitPrice)}</td>
                                                <td className="px-4 py-2 whitespace-nowrap text-sm text-right font-semibold text-gray-800">{formatValue(calculateLineItemTotal(item))}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <p className="text-sm text-gray-500 bg-gray-50 p-3 rounded-lg">
                                No specific line-item details were extracted for this invoice.
                            </p>
                        )}
                    </div>


                    {/* 4. Raw Firestore Data */}
                    <div className="pt-4 border-t">
                        <h3 className="text-xl font-semibold text-gray-700 mb-2">Full Database Object (JSON)</h3>
                        <pre className="text-xs bg-gray-800 text-green-400 p-4 rounded-lg overflow-x-auto max-h-64">
                            {JSON.stringify(invoice, null, 2)}
                        </pre>
                    </div>

                </div>
            </div>
        </div>
    );
};


const ReconciliationTable = ({ invoices, onUpdateStatus, onShowDetails, formatCurrency }) => {
    const calculateVariance = (ourValue, govtValue) => {
        const diff = ourValue - govtValue;
        if (govtValue === 0) {
            return { text: (ourValue === 0) ? '0.00%' : 'N/A', isMismatch: ourValue !== 0 };
        }
        const percentage = (diff / govtValue) * 100;
        return { 
            text: `${percentage.toFixed(2)}%`, 
            isMismatch: Math.abs(percentage) > 1, // Mismatch if greater than 1% variance
            diff: diff.toFixed(2)
        };
    };

    return (
        <div className="overflow-x-auto bg-white rounded-xl shadow-lg">
            <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50 sticky top-0">
                    <tr>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Invoice / Supplier</th>
                        <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Our Taxable Value</th>
                        <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Govt. Taxable Value</th>
                        <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Taxable Variance (%)</th>
                        <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Our ITC (IGST)</th>
                        <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Govt. ITC (IGST)</th>
                        <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">ITC Variance (%)</th>
                        <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Action / Details</th>
                    </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                    {invoices.map((inv) => {
                        const taxableVariance = calculateVariance(inv.taxableValue, inv.govtData?.taxableValue || 0);
                        const itcVariance = calculateVariance(inv.igst, inv.govtData?.igst || 0);

                        const needsReview = inv.status === InvoiceStatus.MISMATCH || inv.status === InvoiceStatus.PENDING;

                        return (
                            <tr key={inv.id} className={needsReview ? 'hover:bg-yellow-50 transition-colors' : 'hover:bg-gray-50 transition-colors'}>
                                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                                    <StatusBadge status={inv.status} />
                                </td>
                                <td className="px-6 py-4 text-sm text-gray-900">
                                    <p className="font-semibold">{inv.invoiceNumber}</p>
                                    <p className="text-xs text-gray-500">{inv.supplierName}</p>
                                    <p className="text-xs text-gray-500 mt-1">GSTIN: {inv.supplierGSTIN.substring(0, 4)}...</p>
                                </td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-center">{formatCurrency(inv.taxableValue)}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-center">{formatCurrency(inv.govtData?.taxableValue || 0)}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm font-semibold text-center" style={{ color: taxableVariance.isMismatch ? '#DC2626' : '#10B981' }}>
                                    {taxableVariance.text}
                                    {taxableVariance.isMismatch && <p className="text-xs font-normal mt-1">({taxableVariance.diff})</p>}
                                </td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-center">{formatCurrency(inv.igst)}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-center">{formatCurrency(inv.govtData?.igst || 0)}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm font-semibold text-center" style={{ color: itcVariance.isMismatch ? '#DC2626' : '#10B981' }}>
                                    {itcVariance.text}
                                    {itcVariance.isMismatch && <p className="text-xs font-normal mt-1">({itcVariance.diff})</p>}
                                </td>
                                <td className="px-6 py-4 whitespace-nowrap text-center">
                                    <div className="flex flex-col space-y-1">
                                        <button
                                            onClick={() => onShowDetails(inv)}
                                            className="text-xs font-medium text-blue-600 hover:text-blue-900 transition-colors"
                                            title="View Full Details"
                                        >
                                            <Eye className="w-4 h-4 inline mr-1" /> Details
                                        </button>
                                        {needsReview && (
                                            <>
                                                <button
                                                    onClick={() => onUpdateStatus(inv, InvoiceStatus.RECONCILED)}
                                                    className="text-xs font-medium text-green-600 hover:text-green-900 transition-colors"
                                                    title="Approve and Reconcile"
                                                >
                                                    <CheckCircle className="w-4 h-4 inline mr-1" /> Reconcile
                                                </button>
                                                <button
                                                    onClick={() => onUpdateStatus(inv, InvoiceStatus.MISMATCH)}
                                                    className="text-xs font-medium text-red-600 hover:text-red-900 transition-colors"
                                                    title="Mark for Manual Review"
                                                >
                                                    <XOctagon className="w-4 h-4 inline mr-1" /> Mismatch
                                                </button>
                                            </>
                                        )}
                                        {!needsReview && (
                                            <span className="text-gray-400 text-xs">Action Complete</span>
                                        )}
                                    </div>
                                </td>
                            </tr>
                        );
                    })}
                    {invoices.length === 0 && (
                        <tr>
                            <td colSpan="9" className="text-center py-10 text-gray-500">
                                <Search className="w-6 h-6 mx-auto mb-2" />
                                No invoices found. Please upload a document using the "Invoice Upload" tab.
                            </td>
                        </tr>
                    )}
                </tbody>
            </table>
        </div>
    );
};


const ReconciliationView = ({ invoices, db, userId, appId }) => {
    const [selectedInvoice, setSelectedInvoice] = useState(null);
    
    const formatCurrency = (amount) => `₹ ${parseFloat(amount).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`;

    const handleUpdateStatus = useCallback(async (invoice, newStatus) => {
        if (!db || !userId) return;

        try {
            const docRef = doc(db, `/artifacts/${appId}/users/${userId}/invoices`, invoice.id);
            await updateDoc(docRef, {
                status: newStatus,
                reconciliationTime: new Date().toISOString(),
            });
        } catch (error) {
            console.error("Failed to update invoice status:", error);
        }
    }, [db, userId, appId]);

    const handleShowDetails = (invoice) => {
        setSelectedInvoice(invoice);
    };

    const handleCloseDetails = () => {
        setSelectedInvoice(null);
    };


    return (
        <div className="p-6 space-y-6">
            <h2 className="text-3xl font-bold text-gray-800">GST Data Reconciliation Table</h2>
            <p className="text-gray-600">
                This table shows **all** uploaded documents. Use the **Details** button to see the full extracted data and the simulated government comparison.
            </p>
            
            <ReconciliationTable 
                invoices={invoices} 
                onUpdateStatus={handleUpdateStatus} 
                onShowDetails={handleShowDetails}
                formatCurrency={formatCurrency} 
            />
            
            <div className="p-4 bg-blue-50 rounded-xl border border-blue-200 text-sm text-blue-800">
                **Note on Variance:** Mismatch is highlighted in red if the variance between Our Data and Govt Data is greater than 1% for either Taxable Value or ITC (IGST).
            </div>

            {/* Modal for Full Details */}
            {selectedInvoice && (
                <InvoiceDetailModal 
                    invoice={selectedInvoice} 
                    onClose={handleCloseDetails} 
                    formatCurrency={formatCurrency} 
                />
            )}
        </div>
    );
};

const FilingView = ({ invoices, db, userId, appId }) => {
    const isReadyToFile = useMemo(() => {
        return !invoices.some(inv => inv.status === InvoiceStatus.PENDING || inv.status === InvoiceStatus.MISMATCH);
    }, [invoices]);

    const summary = useMemo(() => {
        const eligibleITC = invoices
            .filter(inv => inv.status !== InvoiceStatus.FILED && inv.status === InvoiceStatus.RECONCILED)
            .reduce((sum, inv) => sum + parseFloat(inv.igst || 0), 0);
        
        const totalTaxable = invoices
            .filter(inv => inv.status !== InvoiceStatus.FILED)
            .reduce((sum, inv) => sum + parseFloat(inv.taxableValue || 0), 0);

        return {
            totalTaxable,
            eligibleITC
        };
    }, [invoices]);

    const formatCurrency = (amount) => `₹ ${amount.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`;

    const handleFileGST = async () => {
        if (!isReadyToFile || !db || !userId) return;

        try {
            // NOTE: Using window.confirm for a mock action since custom modals are not feasible in this response format.
            // DO NOT use alert() or confirm() in production apps.
            if (!window.confirm("Are you sure you want to simulate filing GSTR-3B?")) {
                return;
            }

            // 1. Simulate the filing process
            window.alert("Simulating API call to GSTN... Filing GSTR-3B...");

            // 2. Update all reconciled/mismatch invoices to 'Filed' status
            const batchUpdates = invoices
                .filter(inv => inv.status !== InvoiceStatus.FILED)
                .map(inv => {
                    const docRef = doc(db, `/artifacts/${appId}/users/${userId}/invoices`, inv.id);
                    return updateDoc(docRef, { status: InvoiceStatus.FILED, filingDate: new Date().toISOString() });
                });

            await Promise.all(batchUpdates);

            window.alert("GSTR-3B Filing Successful! All documents moved to Filed status.");

        } catch (error) {
            console.error("Filing failed:", error);
            window.alert(`Filing failed: ${error.message}`);
        }
    };

    return (
        <div className="p-6 space-y-8">
            <h2 className="text-3xl font-bold text-gray-800">GSTR-3B Filing Preparation</h2>

            <div className="bg-white p-6 rounded-xl shadow-lg border border-gray-100">
                <h3 className="text-xl font-semibold text-gray-800 mb-4 border-b pb-2">Filing Summary (Unfiled Data)</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="p-4 bg-blue-50 rounded-lg">
                        <p className="text-sm text-gray-600">Total Taxable Turnover</p>
                        <p className="text-2xl font-bold text-blue-800">{formatCurrency(summary.totalTaxable)}</p>
                    </div>
                    <div className="p-4 bg-green-50 rounded-lg">
                        <p className="text-sm text-gray-600">Eligible ITC (Claimable)</p>
                        <p className="text-2xl font-bold text-green-800">{formatCurrency(summary.eligibleITC)}</p>
                    </div>
                </div>
                <p className="mt-4 text-xs text-gray-500">
                    *Only reconciled documents are included in the eligible ITC claimable amount.
                </p>
            </div>

            <div className="bg-gray-100 p-6 rounded-xl border border-gray-200 shadow-inner">
                <h3 className="text-xl font-semibold text-gray-800 mb-4 border-b pb-2">Filing Checklist</h3>
                
                {!isReadyToFile && (
                    <div className="space-y-3">
                        <div className="flex items-center text-red-600 font-semibold">
                            <XOctagon className="w-5 h-5 mr-2 flex-shrink-0" />
                            <span className="flex-1">Action Required: Cannot File Yet!</span>
                        </div>
                        <p className="ml-7 text-sm text-red-800 bg-red-50 p-3 rounded-lg">
                            Please resolve all <strong className="font-extrabold">{invoices.filter(inv => inv.status === InvoiceStatus.PENDING).length} Pending</strong> and <strong className="font-extrabold">{invoices.filter(inv => inv.status === InvoiceStatus.MISMATCH).length} Mismatch</strong> documents in the **Reconciliation** tab before proceeding with GSTR-3B.
                        </p>
                    </div>
                )}
                
                {isReadyToFile && (
                    <div className="space-y-3">
                        <div className="flex items-center text-green-600 font-semibold">
                            <CheckCircle className="w-5 h-5 mr-2 flex-shrink-0" />
                            <span className="flex-1">Checklist Complete. Ready to File!</span>
                        </div>
                        <p className="ml-7 text-sm text-green-800 bg-green-50 p-3 rounded-lg">
                            All documents have been reconciled. Proceed to file GSTR-3B.
                        </p>
                    </div>
                )}
            </div>

            <div className="text-center pt-4">
                <button
                    onClick={handleFileGST}
                    disabled={!isReadyToFile}
                    className={`px-10 py-4 rounded-xl font-extrabold text-white text-lg transition-all duration-300 shadow-2xl ${
                        isReadyToFile
                            ? 'bg-blue-700 hover:bg-blue-800 active:scale-95'
                            : 'bg-gray-400 cursor-not-allowed'
                    }`}
                >
                    File GSTR-3B Now
                </button>
            </div>
        </div>
    );
};

// --- 4. MAIN APP COMPONENT ---

const App = () => {
    const { db, auth, userId, isAuthReady, appId } = useFirebase();
    const { invoices, loading } = useInvoices(db, userId, isAuthReady, appId);
    // Set default view to Reconciliation to immediately show the data table
    const [currentView, setCurrentView] = useState(Views.RECONCILIATION); 
    const [isLoginView, setIsLoginView] = useState(true);

    // Determines whether to show the main app or a loading/error state
    useEffect(() => {
        if (!firebaseConfig) {
            console.error("Firebase config is missing.");
            return;
        }
        if (isAuthReady) {
            setIsLoginView(!userId);
        }
    }, [isAuthReady, userId]);

    if (!firebaseConfig) {
        return (
            <div className="text-center p-10">
                <h1 className="text-2xl font-bold text-red-600">Configuration Error</h1>
                <p>Firebase configuration is missing. Cannot run application.</p>
            </div>
        );
    }
    
    // Simple, mock login screen for demonstration
    if (isLoginView && !loading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-gray-100 p-4">
                <div className="bg-white p-10 rounded-xl shadow-2xl w-full max-w-md text-center">
                    <h1 className="text-3xl font-extrabold text-gray-800 mb-4">GST CA Portal Login</h1>
                    <p className="text-gray-600 mb-6">
                        Welcome, Chartered Accountant. Authenticate to access the filing dashboard.
                    </p>
                    <button 
                        onClick={() => {
                             // Attempt to sign in again if the token was available but failed
                            if (initialAuthToken) {
                                signInWithCustomToken(auth, initialAuthToken).catch(console.error);
                            } else {
                                signInAnonymously(auth).catch(console.error);
                            }
                        }}
                        className="w-full py-3 bg-blue-600 text-white font-semibold rounded-xl hover:bg-blue-700 transition-all shadow-lg active:scale-95"
                    >
                        Access Dashboard (Auto-Login)
                    </button>
                    <p className="mt-4 text-xs text-gray-500">
                        This environment uses automatic authentication for demonstration.
                    </p>
                </div>
            </div>
        );
    }

    if (loading || !userId) {
        return (
            <div className="flex items-center justify-center min-h-screen text-xl text-gray-600">
                <RefreshCw className="w-6 h-6 mr-3 animate-spin" />
                Loading application data...
            </div>
        );
    }

    const renderView = () => {
        switch (currentView) {
            case Views.UPLOAD:
                return <UploadView db={db} userId={userId} appId={appId} />;
            case Views.RECONCILIATION:
                return <ReconciliationView invoices={invoices} db={db} userId={userId} appId={appId} />;
            case Views.FILING:
                return <FilingView invoices={invoices} db={db} userId={userId} appId={appId} />;
            case Views.DASHBOARD:
            default:
                return <DashboardView invoices={invoices} />;
        }
    };

    return (
        <div className="min-h-screen bg-gray-50">
            <Navbar userId={userId} currentView={currentView} setCurrentView={setCurrentView} />
            <main className="container mx-auto px-4 py-8">
                <div className="bg-white rounded-xl shadow-2xl overflow-hidden min-h-[80vh]">
                    {renderView()}
                </div>
            </main>
        </div>
    );
};

export default App;
